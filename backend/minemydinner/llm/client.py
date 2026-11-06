import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.constants import EMBEDDING_DIMENSION
from minemydinner.llm.config import ConfigError, LLMConfig, ModelConfig, TaskConfig
from minemydinner.llm.registry import build_chat_model, build_embedding_model
from minemydinner.models import LlmCall

TextHook = Callable[[str], str]

TOKENS_PER_MILLION = Decimal(1_000_000)


class MonthlyCapExceeded(Exception):
    pass


class AllModelsFailed(Exception):
    pass


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    parsed: T
    model_name: str
    fallback_used: bool


def _map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_map_strings(item, transform) for item in value]
    return value


def _estimate_cost(model: ModelConfig, input_tokens: int, output_tokens: int) -> Decimal:
    spent = input_tokens * model.input_usd_per_mtok + output_tokens * model.output_usd_per_mtok
    return spent / TOKENS_PER_MILLION


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

    def invoke_structured[T: BaseModel](
        self, task: str, messages: list[BaseMessage], schema: type[T]
    ) -> StructuredResult[T]:
        task_config = self._task(task)
        self._check_cap(task, task_config)
        prepared = [self._prepare_message(message) for message in messages]
        last_error: Exception | None = None
        for position, name in enumerate(task_config.candidates):
            model = self.config.models[name]
            fallback_used = position > 0
            started = time.perf_counter()
            usage = None
            try:
                chat_model = build_chat_model(model, task_config.timeout_seconds)
                runnable = chat_model.with_structured_output(schema, include_raw=True)
                output = runnable.invoke(prepared)
                usage = output["raw"].usage_metadata
                if output["parsing_error"] is not None:
                    raise output["parsing_error"]
                if output["parsed"] is None:
                    raise ValueError(f"{name} returned no structured result")
                parsed = self._restore(output["parsed"], schema)
            except Exception as error:
                last_error = error
                self._log(task, model, started, usage, False, fallback_used)
                continue
            self._log(task, model, started, usage, True, fallback_used)
            return StructuredResult(parsed, name, fallback_used)
        raise AllModelsFailed(f"All models failed for task {task}") from last_error

    def embed(self, texts: list[str], task: str = "embeddings") -> list[list[float]]:
        if not texts:
            return []
        task_config = self._task(task)
        self._check_cap(task, task_config)
        model = self.config.models[task_config.primary]
        prepared = [self._apply_pre_hooks(text) for text in texts]
        # The embedding API returns no usage, so tokens are estimated.
        usage = {"input_tokens": sum(len(text) // 4 for text in prepared), "output_tokens": 0}
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
        return schema.model_validate(_map_strings(parsed.model_dump(), self._apply_post_hooks))

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
        input_tokens = usage["input_tokens"] if usage else None
        output_tokens = usage["output_tokens"] if usage else None
        with self.session_factory.begin() as session:
            session.add(
                LlmCall(
                    task=task,
                    provider=model.provider,
                    model=model.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_estimate=(
                        _estimate_cost(model, input_tokens, output_tokens) if usage else None
                    ),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    success=success,
                    fallback_used=fallback_used,
                )
            )
