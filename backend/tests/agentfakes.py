"""Fakes for the planning path. No network and no real model."""

import itertools
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from minemydinner.llm.client import LLMClient
from minemydinner.llm.config import load_llm_config
from minemydinner.llm.registry import register_embedding_provider, register_provider
from tests.fakes import write_config
from tests.planningfakes import unit_vector

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
_ids = itertools.count(1)


def tool_call(name: str, args: dict[str, Any], content: str = "") -> AIMessage:
    return AIMessage(content=content, tool_calls=[{"name": name, "args": args, "id": "x"}])


def final_proposal(recipe_ids: list[int], note: str = "Here is your dinner.") -> AIMessage:
    return tool_call("PlanProposal", {"recipe_ids": recipe_ids, "note": note})


class ScriptedChatModel:
    """Stands in for a provider chat model in the planner loop and in structured calls.

    `replies` are returned in order by `invoke`; the last one repeats once the others are
    used. `structured` maps a schema name to its scripted results, used the same way.
    """

    def __init__(
        self,
        replies: list[AIMessage] | None = None,
        structured: dict[str, list[dict[str, Any]]] | None = None,
        error: Exception | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.replies = list(replies or [])
        # Reported on every reply that carries no usage_metadata of its own.
        self.usage = usage or USAGE
        self.structured = {name: list(items) for name, items in (structured or {}).items()}
        self.error = error
        self.sent: list[list[BaseMessage]] = []
        self.bound: list[dict[str, Any]] = []

    def bind_tools(self, tools, **kwargs) -> "ScriptedChatModel":
        self.bound.append(kwargs)
        return self

    def invoke(self, messages, config=None, **kwargs) -> AIMessage:
        self.sent.append(list(messages))
        if self.error is not None:
            raise self.error
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        calls = [{**call, "id": f"call-{next(_ids)}"} for call in reply.tool_calls]
        usage = reply.usage_metadata or self.usage
        return AIMessage(
            content=reply.content,
            tool_calls=calls,
            usage_metadata=usage,
            response_metadata=reply.response_metadata,
        )

    def with_structured_output(self, schema: type, include_raw: bool = False):
        assert include_raw
        return _Structured(self, schema)


class _Structured:
    def __init__(self, model: ScriptedChatModel, schema: type) -> None:
        self.model = model
        self.schema = schema

    def invoke(self, messages) -> dict[str, Any]:
        self.model.sent.append(list(messages))
        if self.model.error is not None:
            raise self.model.error
        items = self.model.structured[self.schema.__name__]
        item = items.pop(0) if len(items) > 1 else items[0]
        raw = AIMessage(content="{}", usage_metadata=USAGE)
        return {"raw": raw, "parsed": self.schema.model_validate(item), "parsing_error": None}


class FakeEmbeddings:
    """Stands in for the embedding model, so a query is never sent to a provider."""

    def __init__(self, vector: list[float] | None = None, error: Exception | None = None):
        self.vector = vector if vector is not None else unit_vector(0)
        self.error = error
        self.embedded: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.error is not None:
            raise self.error
        self.embedded.extend(texts)
        return [list(self.vector) for _ in texts]


def make_llm_client(
    tmp_path,
    session_factory,
    primary: ScriptedChatModel,
    fallback: ScriptedChatModel | None = None,
    embeddings: FakeEmbeddings | None = None,
    **hooks,
) -> LLMClient:
    """A real LLMClient whose planning primary (deepseek), fallback (openai), and embedding
    model are fakes."""
    embedder = embeddings or FakeEmbeddings()
    register_provider("agent_primary", lambda model, timeout: primary)
    register_provider("agent_fallback", lambda model, timeout: fallback or primary)
    register_embedding_provider("agent_embed", lambda model, timeout: embedder)
    path = write_config(
        tmp_path / "models.yaml",
        providers={
            "deepseek": "agent_primary",
            "openai": "agent_fallback",
            "gemini_embedding": "agent_embed",
        },
    )
    return LLMClient(load_llm_config(path), session_factory, Decimal("20"), **hooks)
