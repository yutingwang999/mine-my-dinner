from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from minemydinner.agent.planner import PLANNER_INPUT_TOKEN_BUDGET
from minemydinner.llm.config import ConfigError, LLMConfig, load_llm_config
from minemydinner.settings import Settings
from tests.fakes import write_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


@pytest.fixture
def tmp_config(tmp_path):
    return lambda **changes: write_config(tmp_path / "models.yaml", **changes)


def provider_of(config: LLMConfig, task: str, slot: str) -> str:
    return config.models[getattr(config.tasks[task], slot)].provider


def test_rejects_model_without_required_capability(tmp_config):
    with pytest.raises(ConfigError, match="strict_json"):
        load_llm_config(tmp_config(primary="deepseek", requires=["strict_json"]))


def test_rejects_fallback_without_required_capability(tmp_config):
    with pytest.raises(ConfigError, match="tool_calling"):
        load_llm_config(tmp_config(fallback="gemini", task="planning", requires=["tool_calling"]))


def test_rejects_model_without_price(tmp_config):
    with pytest.raises(ConfigError):
        load_llm_config(tmp_config(drop_price="gemini"))


def test_rejects_task_naming_an_unknown_model(tmp_config):
    with pytest.raises(ConfigError, match="missing"):
        load_llm_config(tmp_config(primary="missing"))


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_llm_config(tmp_path / "absent.yaml")


def test_override_switches_model_id_and_its_catalog_price(tmp_config):
    config = load_llm_config(tmp_config(), model_overrides={"gemini": "gemini-alt"})
    assert config.models["gemini"].model == "gemini-alt"
    assert config.models["gemini"].input_usd_per_mtok == Decimal("3.0")


def test_empty_override_keeps_the_default(tmp_config):
    config = load_llm_config(tmp_config(), model_overrides={"gemini": ""})
    assert config.models["gemini"].model == "gemini-test"


def test_rejects_override_missing_from_catalog(tmp_config):
    with pytest.raises(ConfigError, match="gemini-9-unknown"):
        load_llm_config(tmp_config(), model_overrides={"gemini": "gemini-9-unknown"})


def test_rejects_override_without_required_capability(tmp_config):
    with pytest.raises(ConfigError, match="strict_json"):
        load_llm_config(tmp_config(), model_overrides={"gemini": "deepseek-test"})


def test_rejects_override_for_unknown_slot(tmp_config):
    with pytest.raises(ConfigError, match="mistral"):
        load_llm_config(tmp_config(), model_overrides={"mistral": "gemini-test"})


def test_settings_read_model_overrides_from_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    overrides = Settings(_env_file=None).model_overrides()
    assert overrides["deepseek"] == "deepseek-v4-pro"
    assert set(overrides) == {"gemini", "openai", "deepseek", "gemini_embedding"}


def test_every_repository_model_choice_is_valid():
    catalog = yaml.safe_load(REPO_CONFIG.read_text(encoding="utf-8"))["catalog"]
    for model_id in catalog:
        provider = catalog[model_id]["provider"]
        slot = {"google_genai": "gemini", "openai": "openai", "deepseek": "deepseek"}[provider]
        if "embeddings" in catalog[model_id]["capabilities"]:
            slot = "gemini_embedding"
        load_llm_config(REPO_CONFIG, model_overrides={slot: model_id})


def test_embeddings_cannot_have_a_fallback(tmp_config):
    with pytest.raises(ConfigError, match="fallback"):
        load_llm_config(tmp_config(task="embeddings", fallback="openai"))


def test_repository_config_matches_design():
    config = load_llm_config(REPO_CONFIG)
    assert provider_of(config, "extraction", "primary") == "google_genai"
    assert provider_of(config, "extraction", "fallback") == "openai"
    assert provider_of(config, "planning", "primary") == "deepseek"
    assert provider_of(config, "planning", "fallback") == "openai"
    assert config.tasks["embeddings"].fallback is None
    assert config.models[config.tasks["embeddings"].primary].model == "gemini-embedding-001"


def test_repository_config_lists_every_task_and_only_planning_is_essential():
    config = load_llm_config(REPO_CONFIG)
    assert set(config.tasks) == {
        "extraction",
        "image_understanding",
        "allergen_suggestions",
        "planning",
        "planning_eval",
        "embeddings",
        "smoke",
    }
    assert {name for name, task in config.tasks.items() if task.essential} == {"planning"}


def test_repository_config_sets_tool_options_for_openai_fallback():
    cfg = load_llm_config(REPO_CONFIG)
    assert cfg.models["openai"].tool_call_options == {"reasoning_effort": "none"}


def test_repository_config_has_non_essential_planning_eval():
    cfg = load_llm_config(REPO_CONFIG)
    planning, evaluation = cfg.tasks["planning"], cfg.tasks["planning_eval"]
    assert evaluation.essential is False
    assert evaluation.candidates == planning.candidates
    assert evaluation.requires == planning.requires
    assert evaluation.timeout_seconds == planning.timeout_seconds


def test_chat_models_declare_cached_price_and_context_window():
    cfg = load_llm_config(REPO_CONFIG)
    for model in cfg.models.values():
        if "embeddings" not in model.capabilities:
            assert model.cached_input_usd_per_mtok is not None and model.context_window


def test_planner_budget_is_at_most_half_of_every_planning_window():
    cfg = load_llm_config(REPO_CONFIG)
    for task in ("planning", "planning_eval"):
        for name in cfg.tasks[task].candidates:
            assert PLANNER_INPUT_TOKEN_BUDGET * 2 <= cfg.models[name].context_window


def test_chat_model_without_cached_price_is_rejected(tmp_config):
    with pytest.raises(ConfigError):
        load_llm_config(tmp_config(drop_cached_price="gemini"))
