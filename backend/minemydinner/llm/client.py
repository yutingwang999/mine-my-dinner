import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.constants import EMBEDDING_DIMENSION
from minemydinner.llm.config import ConfigError, LLMConfig, ModelConfig, TaskConfig
from minemydinner.llm.registry import build_chat_model, build_embedding_model
from minemydinner.models import LlmCall

logger = logging.getLogger(__name__)

TextHook = Callable[[str], str]

TOKENS_PER_MILLION = Decimal(1_000_000)


class MonthlyCapExceeded(Exception):
    pass


class AllModelsFailed(Exception):
    pass


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    parsed: T
    # The configured name and the model ID, such as "gemini:gemini-3.5-flash-lite".
    model_name: str
    fallback_used: bool
    # The provider key of the model that answered, and the model name the provider reports
    # having served (None when it reports none). Recorded in planning records.
    provider: str | None = None
    served_model: str | None = None


@dataclass(frozen=True)
class CallOutcome[R]:
    value: R
    # The configured name and the model ID, such as "gemini:gemini-3.5-flash-lite".
    model_name: str
    fallback_used: bool


# Given the candidate's config and a built chat model, returns the result and its usage.
type Attempt[R] = Callable[[ModelConfig, BaseChatModel], tuple[R, dict[str, int] | None]]


class AttemptError(Exception):
    """Raised by an attempt that failed after the provider billed it; carries the usage."""

    def __init__(self, message: str, usage: dict[str, int] | None) -> None:
        super().__init__(message)
        self.usage = usage


def map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    """Apply `transform` to every string inside nested dicts, lists, and tuples."""
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [map_strings(item, transform) for item in value]
    return value


def normalize_usage(usage: Mapping[str, Any] | None) -> dict[str, int] | None:
    """Reduce LangChain usage_metadata to the four counts that llm_calls stores.

    `input_tokens` is the total, cached and written tokens included. The cache counts
    are 0 when the provider reports none, and never add up to more than the total.
    """
    if usage is None:
        return None
    total = usage.get("input_tokens") or 0
    details = usage.get("input_token_details") or {}
    read = min(details.get("cache_read") or 0, total)
    written = min(details.get("cache_creation") or 0, total - read)
    return {
        "input_tokens": total,
        "cached_input_tokens": read,
        "cache_write_input_tokens": written,
        "output_tokens": usage.get("output_tokens") or 0,
    }


def _estimate_cost(model: ModelConfig, usage: dict[str, int]) -> Decimal:
    read = usage["cached_input_tokens"]
    written = usage["cache_write_input_tokens"]
    plain = usage["input_tokens"] - read - written
    write_price = model.cache_write_usd_per_mtok
    if write_price is None:
        write_price = model.input_usd_per_mtok
    cached_price = model.cached_input_usd_per_mtok
    if cached_price is None:
        cached_price = model.input_usd_per_mtok
    spent = (
        plain * model.input_usd_per_mtok
        + written * write_price
        + read * cached_price
        + usage["output_tokens"] * model.output_usd_per_mtok
    )
    return spent / TOKENS_PER_MILLION


def served_model_name(reply: Any) -> str | None:
    """The provider-reported model name (langchain-openai, -deepseek, and -google-genai all
    use `model_name` in the response metadata), or None."""
    metadata = getattr(reply, "response_metadata", None) or {}
    value = metadata.get("model_name") or metadata.get("model")
    return str(value) if value else None


class LLMClient:
    """Calls models by task, with failover, a monthly cap, and a log of every call."""

    def __init__(
        self,
        config: LLMConfig,
        session_factory: sessionmaker[Session],
        monthly_cap_usd: Decimal,
        pre_hooks: Sequence[TextHook] = (),
        post_hooks: Sequence[TextHook] = (),
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.monthly_cap_usd = monthly_cap_usd
        self.pre_hooks = pre_hooks
        self.post_hooks = post_hooks

    def run_task[R](self, task: str, attempt: Attempt[R]) -> CallOutcome[R]:
        task_config = self._task(task)
        self._check_cap(task, task_config)
        last_error: Exception | None = None
        for position, name in enumerate(task_config.candidates):
            model = self.config.models[name]
            fallback_used = position > 0
            started = time.perf_counter()
            usage = None
            try:
                chat_model = build_chat_model(model, task_config.timeout_seconds)
                value, usage = attempt(model, chat_model)
            except MonthlyCapExceeded:
                raise
            except Exception as error:
                if isinstance(error, AttemptError):
                    usage = error.usage
                    last_error = error.__cause__ or error
                else:
                    last_error = error
                self._log(task, model, started, usage, False, fallback_used)
                continue
            self._log(task, model, started, usage, True, fallback_used)
            # The model ID too, so results stay distinct after a model is swapped.
            return CallOutcome(value, f"{name}:{model.model}", fallback_used)
        raise AllModelsFailed(f"All models failed for task {task}") from last_error

    def invoke_structured[T: BaseModel](
        self, task: str, messages: list[BaseMessage], schema: type[T]
    ) -> StructuredResult[T]:
        prepared = self.prepare_messages(messages)
        used: dict[str, str | None] = {}

        def attempt(
            model: ModelConfig, chat_model: BaseChatModel
        ) -> tuple[T, dict[str, int] | None]:
            runnable = chat_model.with_structured_output(schema, include_raw=True)
            output = runnable.invoke(prepared)
            used["provider"] = model.provider
            used["served_model"] = served_model_name(output["raw"])
            usage = normalize_usage(output["raw"].usage_metadata)
            try:
                if output["parsing_error"] is not None:
                    raise output["parsing_error"]
                if output["parsed"] is None:
                    raise ValueError(f"{model.model} returned no structured result")
                return self._restore(output["parsed"], schema), usage
            except Exception as error:
                # The provider billed this attempt, so its usage still counts.
                raise AttemptError(str(error), usage) from error

        outcome = self.run_task(task, attempt)
        return StructuredResult(
            outcome.value,
            outcome.model_name,
            outcome.fallback_used,
            used.get("provider"),
            used.get("served_model"),
        )

    def prepare_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Apply the pre-hooks to a copy of the messages."""
        return [self._prepare_message(message) for message in messages]

    def prepare_text(self, text: str) -> str:
        """Apply the pre-hooks to text bound for a model."""
        return self._apply_pre_hooks(text)

    def restore_text(self, text: str) -> str:
        """Apply the post-hooks to text from a model."""
        return self._apply_post_hooks(text)

    def embed(self, texts: list[str], task: str = "embeddings") -> list[list[float]]:
        if not texts:
            return []
        task_config = self._task(task)
        self._check_cap(task, task_config)
        model = self.config.models[task_config.primary]
        prepared = [self._apply_pre_hooks(text) for text in texts]
        # The embedding API returns no usage, so tokens are estimated.
        usage = {
            "input_tokens": sum(len(text) // 4 for text in prepared),
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 0,
        }
        started = time.perf_counter()
        try:
            embedder = build_embedding_model(model, task_config.timeout_seconds)
            vectors = embedder.embed_documents(prepared)
            if len(vectors) != len(prepared) or any(
                len(vector) != EMBEDDING_DIMENSION for vector in vectors
            ):
                raise ValueError(f"Expected {len(prepared)} vectors of {EMBEDDING_DIMENSION}")
        except Exception as error:
            self._log(task, model, started, usage, False, False)
            raise AllModelsFailed(f"Embedding failed for task {task}") from error
        self._log(task, model, started, usage, True, False)
        return vectors

    def _task(self, task: str) -> TaskConfig:
        task_config = self.config.tasks.get(task)
        if task_config is None:
            raise ConfigError(f"Unknown task {task}")
        return task_config

    def _check_cap(self, task: str, task_config: TaskConfig) -> None:
        if task_config.essential:
            return
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = select(func.coalesce(func.sum(LlmCall.cost_estimate), 0)).where(
            LlmCall.created_at >= month_start
        )
        with self.session_factory() as session:
            spent = session.scalar(query)
        if spent >= self.monthly_cap_usd:
            raise MonthlyCapExceeded(
                f"Spent {spent} of the {self.monthly_cap_usd} USD monthly cap; {task} is stopped"
            )

    def _apply_pre_hooks(self, text: str) -> str:
        for hook in self.pre_hooks:
            text = hook(text)
        return text

    def _apply_post_hooks(self, text: str) -> str:
        for hook in self.post_hooks:
            text = hook(text)
        return text

    def _prepare_message(self, message: BaseMessage) -> BaseMessage:
        content = message.content
        if isinstance(content, str):
            return message.model_copy(update={"content": self._apply_pre_hooks(content)})
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(self._apply_pre_hooks(part))
            elif part.get("type") == "text":
                parts.append({**part, "text": self._apply_pre_hooks(part["text"])})
            else:
                parts.append(part)
        return message.model_copy(update={"content": parts})

    def _restore[T: BaseModel](self, parsed: T, schema: type[T]) -> T:
        if not self.post_hooks:
            return parsed
        return schema.model_validate(map_strings(parsed.model_dump(), self._apply_post_hooks))

    def _log(
        self,
        task: str,
        model: ModelConfig,
        started: float,
        usage: dict[str, int] | None,
        success: bool,
        fallback_used: bool,
    ) -> None:
        """Commit in a session of its own, so a business rollback keeps the record."""
        row = LlmCall(
            task=task,
            provider=model.provider,
            model=model.model,
            input_tokens=usage["input_tokens"] if usage else None,
            cached_input_tokens=usage["cached_input_tokens"] if usage else None,
            cache_write_input_tokens=usage["cache_write_input_tokens"] if usage else None,
            output_tokens=usage["output_tokens"] if usage else None,
            cost_estimate=_estimate_cost(model, usage) if usage else None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            success=success,
            fallback_used=fallback_used,
        )
        try:
            with self.session_factory.begin() as session:
                session.add(row)
        except Exception:
            # A paid result must still be returned, so a failed log write is only logged.
            logger.exception("Could not write the llm_calls row for task %s", task)
