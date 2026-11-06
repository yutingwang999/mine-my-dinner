from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage, BaseMessage

CATALOG_DEFAULTS = {
    "gemini-test": ("google_genai", ["strict_json", "images"], 1.0, 2.0),
    "gemini-alt": ("google_genai", ["strict_json", "images"], 3.0, 4.0),
    "openai-test": ("openai", ["strict_json", "images", "tool_calling", "embeddings"], 1.0, 2.0),
    "deepseek-test": ("deepseek", ["tool_calling"], 1.0, 2.0),
    "embedding-test": ("google_genai", ["embeddings"], 1.0, 2.0),
}
MODEL_DEFAULTS = {
    "gemini": "gemini-test",
    "openai": "openai-test",
    "deepseek": "deepseek-test",
    "gemini_embedding": "embedding-test",
}


def write_config(
    path: Path,
    task: str = "extraction",
    primary: str | None = None,
    fallback: str | None = None,
    requires: list[str] | None = None,
    drop_price: str | None = None,
    primary_provider: str | None = None,
    providers: dict[str, str] | None = None,
) -> Path:
    """Write a valid model config, changed only by the arguments given."""
    catalog: dict[str, dict[str, Any]] = {
        model: {
            "provider": provider,
            "capabilities": capabilities,
            "input_usd_per_mtok": input_price,
            "output_usd_per_mtok": output_price,
        }
        for model, (provider, capabilities, input_price, output_price) in CATALOG_DEFAULTS.items()
    }
    for name, provider in (providers or {}).items():
        catalog[MODEL_DEFAULTS[name]]["provider"] = provider
    tasks: dict[str, dict[str, Any]] = {
        "extraction": {"primary": "gemini", "fallback": "openai", "requires": ["strict_json"]},
        "image_understanding": {"primary": "gemini", "fallback": "openai", "requires": ["images"]},
        "planning": {"primary": "deepseek", "fallback": "openai", "essential": True},
        "embeddings": {"primary": "gemini_embedding", "requires": ["embeddings"]},
    }
    for settings in tasks.values():
        settings["timeout_seconds"] = 30
    if primary is not None:
        tasks[task]["primary"] = primary
    if fallback is not None:
        tasks[task]["fallback"] = fallback
    if requires is not None:
        tasks[task]["requires"] = requires
    if drop_price is not None:
        del catalog[MODEL_DEFAULTS[drop_price]]["input_usd_per_mtok"]
    if primary_provider is not None:
        catalog[MODEL_DEFAULTS[tasks[task]["primary"]]]["provider"] = primary_provider
    document = {
        "prices_updated": "2026-11-03",
        "catalog": catalog,
        "models": MODEL_DEFAULTS,
        "tasks": tasks,
    }
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def payload_for(schema: type) -> dict[str, Any]:
    note = {"note": "Diner 1 eats"}
    return {"items": [note]} if "items" in schema.model_fields else note


class FakeChatModel:
    """Stands in for a provider model and records what it was sent."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.parsing_error: Exception | None = None
        self.returns_nothing = False
        self.call_count = 0
        self.last_text: str | None = None
        self.last_image_url: str | None = None

    def with_structured_output(self, schema: type, include_raw: bool = False) -> "_FakeRunnable":
        assert include_raw
        return _FakeRunnable(self, schema)

    def record(self, messages: list[BaseMessage]) -> None:
        self.call_count += 1
        for message in messages:
            if isinstance(message.content, str):
                self.last_text = message.content
                continue
            for part in message.content:
                if part["type"] == "text":
                    self.last_text = part["text"]
                else:
                    self.last_image_url = part["image_url"]["url"]


class _FakeRunnable:
    def __init__(self, model: FakeChatModel, schema: type) -> None:
        self.model = model
        self.schema = schema

    def invoke(self, messages: list[BaseMessage]) -> dict[str, Any]:
        self.model.record(messages)
        if self.model.error is not None:
            raise self.model.error
        raw = AIMessage(
            content="{}",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        if self.model.parsing_error is not None:
            return {"raw": raw, "parsed": None, "parsing_error": self.model.parsing_error}
        if self.model.returns_nothing:
            return {"raw": raw, "parsed": None, "parsing_error": None}
        parsed = self.schema.model_validate(payload_for(self.schema))
        return {"raw": raw, "parsed": parsed, "parsing_error": None}


class FakeEmbeddings:
    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension
        self.error: Exception | None = None
        self.last_texts: list[str] | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.last_texts = texts
        if self.error is not None:
            raise self.error
        return [[0.1] * self.dimension for _ in texts]
