from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

Capability = Literal[
    "tool_calling", "strict_json", "images", "video", "speech_to_text", "embeddings"
]


class ConfigError(Exception):
    pass


class CatalogEntry(BaseModel):
    provider: str
    capabilities: list[Capability]
    input_usd_per_mtok: Decimal = Field(ge=0)
    output_usd_per_mtok: Decimal = Field(ge=0)
    # Extra keyword arguments for the provider's chat model class.
    options: dict[str, Any] = {}


class ModelConfig(CatalogEntry):
    model: str


class TaskConfig(BaseModel):
    primary: str
    fallback: str | None = None
    requires: list[Capability] = []
    essential: bool = False
    timeout_seconds: int = Field(gt=0)

    @property
    def candidates(self) -> list[str]:
        return [self.primary, self.fallback] if self.fallback else [self.primary]


class ConfigFile(BaseModel):
    """models.yaml as written: each model name picks a model ID from the catalog."""

    prices_updated: date
    catalog: dict[str, CatalogEntry]
    models: dict[str, str]
    tasks: dict[str, TaskConfig]


class LLMConfig(BaseModel):
    prices_updated: date
    models: dict[str, ModelConfig]
    tasks: dict[str, TaskConfig]


def resolve_models(
    config_file: ConfigFile, model_overrides: Mapping[str, str | None]
) -> dict[str, ModelConfig]:
    unknown = set(model_overrides) - set(config_file.models)
    if unknown:
        raise ConfigError(f"Model overrides name unknown models {sorted(unknown)}")
    models = {}
    for name, default_id in config_file.models.items():
        model_id = model_overrides.get(name) or default_id
        entry = config_file.catalog.get(model_id)
        if entry is None:
            raise ConfigError(f"Model ID {model_id} for {name} needs a catalog entry with prices")
        models[name] = ModelConfig(model=model_id, **entry.model_dump())
    return models


def validate_task(name: str, task: TaskConfig, models: dict[str, ModelConfig]) -> None:
    if "embeddings" in task.requires and task.fallback:
        raise ConfigError(f"Task {name} needs embeddings, which cannot have a fallback")
    for candidate in task.candidates:
        if candidate not in models:
            raise ConfigError(f"Task {name} names unknown model {candidate}")
        missing = set(task.requires) - set(models[candidate].capabilities)
        if missing:
            raise ConfigError(f"Model {candidate} lacks {sorted(missing)} for task {name}")


def load_llm_config(
    path: Path = CONFIG_PATH, model_overrides: Mapping[str, str | None] | None = None
) -> LLMConfig:
    """Load models.yaml. model_overrides maps a model name to a catalog model ID."""
    try:
        config_file = ConfigFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigError(f"Cannot load model config {path}: {error}") from error
    config = LLMConfig(
        prices_updated=config_file.prices_updated,
        models=resolve_models(config_file, model_overrides or {}),
        tasks=config_file.tasks,
    )
    for name, task in config.tasks.items():
        validate_task(name, task, config.models)
    return config
