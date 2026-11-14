import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from minemydinner.llm.client import (
    AllModelsFailed,
    LLMClient,
    MonthlyCapExceeded,
    _estimate_cost,
    normalize_usage,
)
from minemydinner.llm.config import CONFIG_PATH as REPO_CONFIG
from minemydinner.llm.config import ConfigError, ModelConfig, load_llm_config
from minemydinner.llm.registry import (
    build_chat_model,
    build_embedding_model,
    register_embedding_provider,
    register_provider,
)
from minemydinner.models import LlmCall
from minemydinner.settings import Settings
from tests.fakes import FakeChatModel, FakeEmbeddings, write_config

IMAGE_URL = "data:image/png;base64,AAAA"
MESSAGES = [HumanMessage("小李 eats")]
IMAGE_AND_TEXT_MESSAGE = HumanMessage(
    [
        {"type": "text", "text": "小李 eats"},
        {"type": "image_url", "image_url": {"url": IMAGE_URL}},
    ]
)
FAKE_PROVIDERS = {
    "gemini": "fake_primary",
    "openai": "fake_fallback",
    "deepseek": "fake_primary",
    "gemini_embedding": "fake_embedding",
}


class Schema(BaseModel):
    note: str


class NestedSchema(BaseModel):
    items: list[Schema]


def anonymize(text: str) -> str:
    return text.replace("小李", "Diner 1")


def restore(text: str) -> str:
    return text.replace("Diner 1", "小李")


# Rows kept in the test database (evaluations, real-app checks) must survive these tests, so the
# fixture removes only rows created after its starting watermark and the helpers ignore older rows.
_WATERMARK = {"id": 0}


@pytest.fixture
def session_factory(engine):
    """Real commits, because the call log must commit apart from business work."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as probe:
        _WATERMARK["id"] = probe.scalar(select(func.coalesce(func.max(LlmCall.id), 0)))
    yield factory
    with factory.begin() as cleanup:
        cleanup.execute(delete(LlmCall).where(LlmCall.id > _WATERMARK["id"]))


@pytest.fixture
def primary_fake():
    fake = FakeChatModel()
    register_provider("fake_primary", lambda model, timeout: fake)
    return fake


@pytest.fixture
def fallback_fake():
    fake = FakeChatModel()
    register_provider("fake_fallback", lambda model, timeout: fake)
    return fake


@pytest.fixture
def embedding_fake():
    fake = FakeEmbeddings()
    register_embedding_provider("fake_embedding", lambda model, timeout: fake)
    return fake


@pytest.fixture
def make_client(tmp_path, session_factory, primary_fake, fallback_fake, embedding_fake):
    def make(**hooks):
        path = write_config(tmp_path / "models.yaml", providers=FAKE_PROVIDERS)
        return LLMClient(load_llm_config(path), session_factory, Decimal("20"), **hooks)

    return make


@pytest.fixture
def llm(make_client):
    return make_client()


@pytest.fixture
def llm_primary_fails(llm, primary_fake):
    primary_fake.error = RuntimeError("provider down")
    return llm


@pytest.fixture
def llm_primary_bad_json(llm, primary_fake):
    primary_fake.parsing_error = ValueError("not valid JSON")
    return llm


@pytest.fixture
def llm_primary_returns_nothing(llm, primary_fake):
    primary_fake.returns_nothing = True
    return llm


@pytest.fixture
def llm_all_fail(llm, primary_fake, fallback_fake):
    primary_fake.error = RuntimeError("provider down")
    fallback_fake.error = RuntimeError("provider down")
    return llm


def add_spend(session_factory, cost: str, created_at: datetime | None = None) -> None:
    row = LlmCall(
        task="extraction",
        provider="fake",
        model="fake",
        cost_estimate=Decimal(cost),
        success=True,
    )
    if created_at is not None:
        row.created_at = created_at
    with session_factory.begin() as session:
        session.add(row)


@pytest.fixture
def llm_over_cap(make_client, session_factory):
    add_spend(session_factory, "20")
    return make_client()


@pytest.fixture
def llm_with_last_month_spend(make_client, session_factory):
    first_of_month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    add_spend(session_factory, "20", first_of_month - timedelta(seconds=1))
    return make_client()


@pytest.fixture
def llm_hooked(make_client):
    return make_client(pre_hooks=[anonymize], post_hooks=[restore])


@pytest.fixture
def llm_hooked_primary_fails(llm_hooked, primary_fake):
    primary_fake.error = RuntimeError("provider down")
    return llm_hooked


@pytest.fixture
def llm_embed_fails(llm, embedding_fake):
    embedding_fake.error = RuntimeError("provider down")
    return llm


@pytest.fixture
def llm_embed_wrong_size(llm, embedding_fake):
    embedding_fake.dimension = 3072
    return llm


def calls(session):
    return list(session.scalars(select(LlmCall).where(LlmCall.id > _WATERMARK["id"]).order_by(LlmCall.id))
    )


def only_call(session):
    rows = calls(session)
    assert len(rows) == 1
    return rows[0]


def count_calls(session_factory):
    with session_factory() as session:
        return session.scalar(
            select(func.count()).select_from(LlmCall).where(LlmCall.id > _WATERMARK["id"])
        )


def test_primary_success_logs_one_call(llm, session):
    result = llm.invoke_structured("extraction", MESSAGES, Schema)
    assert result.fallback_used is False
    # Name and model ID, so logs stay distinct after GEMINI_MODEL changes.
    assert result.model_name == "gemini:gemini-test"
    call = only_call(session)
    assert (call.task, call.success, call.fallback_used) == ("extraction", True, False)
    assert (call.provider, call.model) == ("fake_primary", "gemini-test")
    assert call.input_tokens == 10 and call.output_tokens == 5
    assert call.latency_ms is not None
    assert call.cost_estimate == Decimal("0.00002")


def test_failure_switches_to_fallback_and_logs_both(llm_primary_fails, session):
    result = llm_primary_fails.invoke_structured("extraction", MESSAGES, Schema)
    assert result.fallback_used is True and result.model_name == "openai:openai-test"
    first, second = calls(session)
    assert first.success is False and first.fallback_used is False
    assert second.success is True and second.fallback_used is True


def test_schema_parse_failure_also_fails_over(llm_primary_bad_json, session):
    result = llm_primary_bad_json.invoke_structured("extraction", MESSAGES, Schema)
    assert result.fallback_used is True
    # The provider billed the failed attempt, so its cost still counts.
    assert calls(session)[0].cost_estimate > 0


def test_empty_result_without_parsing_error_fails_over(llm_primary_returns_nothing):
    result = llm_primary_returns_nothing.invoke_structured("extraction", MESSAGES, Schema)
    assert result.fallback_used is True


def test_all_models_failing_raises_and_is_logged(llm_all_fail, session):
    with pytest.raises(AllModelsFailed):
        llm_all_fail.invoke_structured("extraction", MESSAGES, Schema)
    assert len(calls(session)) == 2


def test_missing_builder_counts_as_a_failure_and_fails_over(
    tmp_path, session_factory, fallback_fake
):
    providers = {**FAKE_PROVIDERS, "gemini": "never_registered"}
    config = load_llm_config(write_config(tmp_path / "models.yaml", providers=providers))
    client = LLMClient(config, session_factory, Decimal("20"))
    assert client.invoke_structured("extraction", MESSAGES, Schema).fallback_used is True


def test_unknown_task_is_a_config_error(llm):
    with pytest.raises(ConfigError):
        llm.invoke_structured("no_such_task", MESSAGES, Schema)


def test_monthly_cap_stops_non_essential_without_calling_model(llm_over_cap, primary_fake):
    with pytest.raises(MonthlyCapExceeded):
        llm_over_cap.invoke_structured("extraction", MESSAGES, Schema)
    assert primary_fake.call_count == 0


def test_monthly_cap_stops_embeddings_without_calling_model(llm_over_cap, embedding_fake):
    with pytest.raises(MonthlyCapExceeded):
        llm_over_cap.embed(["x"])
    assert embedding_fake.last_texts is None


def test_monthly_cap_allows_essential(llm_over_cap):
    assert llm_over_cap.invoke_structured("planning", MESSAGES, Schema).parsed


def test_cap_counts_only_the_current_utc_month(llm_with_last_month_spend):
    assert llm_with_last_month_spend.invoke_structured("extraction", MESSAGES, Schema).parsed


def test_pre_hook_reaches_primary_and_fallback(
    llm_hooked_primary_fails, primary_fake, fallback_fake
):
    llm_hooked_primary_fails.invoke_structured("extraction", [HumanMessage("小李 eats")], Schema)
    assert "Diner 1" in primary_fake.last_text and "Diner 1" in fallback_fake.last_text
    assert "小李" not in primary_fake.last_text and "小李" not in fallback_fake.last_text


def test_pre_hook_keeps_image_parts(llm_hooked, primary_fake):
    llm_hooked.invoke_structured("image_understanding", [IMAGE_AND_TEXT_MESSAGE], Schema)
    assert primary_fake.last_image_url == IMAGE_URL
    assert primary_fake.last_text == "Diner 1 eats"


def test_pre_hook_leaves_the_callers_messages_unchanged(llm_hooked):
    message = HumanMessage("小李 eats")
    llm_hooked.invoke_structured("extraction", [message], Schema)
    assert message.content == "小李 eats"


def test_pre_hook_reaches_embedding_text(llm_hooked, embedding_fake):
    llm_hooked.embed(["小李 eats"])
    assert embedding_fake.last_texts == ["Diner 1 eats"]


def test_post_hook_restores_string_fields_in_nested_results(llm_hooked):
    result = llm_hooked.invoke_structured("extraction", MESSAGES, NestedSchema)
    assert result.parsed.items[0].note == "小李 eats"


def test_new_provider_needs_only_registration(tmp_path, session_factory):
    register_provider("qwen_local", lambda model, timeout: FakeChatModel())
    config = load_llm_config(write_config(tmp_path / "models.yaml", primary_provider="qwen_local"))
    client = LLMClient(config, session_factory, Decimal("20"))
    assert client.invoke_structured("extraction", MESSAGES, Schema).parsed


def test_embed_returns_768_and_logs_estimated_tokens(llm, session):
    vectors = llm.embed(["x" * 40])
    assert [len(vector) for vector in vectors] == [768]
    call = only_call(session)
    assert (call.task, call.success, call.input_tokens) == ("embeddings", True, 10)
    assert call.cost_estimate > 0


def test_embed_returns_768_and_has_no_failover(llm_embed_fails, session):
    with pytest.raises(AllModelsFailed):
        llm_embed_fails.embed(["x"])
    assert len(calls(session)) == 1


def test_embed_rejects_wrong_dimension(llm_embed_wrong_size, session):
    with pytest.raises(AllModelsFailed):
        llm_embed_wrong_size.embed(["x"])
    assert only_call(session).success is False


def test_call_log_survives_business_rollback(llm, session_factory):
    with session_factory() as business:
        llm.invoke_structured("extraction", MESSAGES, Schema)
        business.rollback()
    assert count_calls(session_factory) == 1


@pytest.fixture
def fake_keys(monkeypatch):
    settings = Settings(
        _env_file=None,
        google_api_key="test-google",
        openai_api_key="test-openai",
        deepseek_api_key="test-deepseek",
    )
    monkeypatch.setattr("minemydinner.llm.registry.get_settings", lambda: settings)


@pytest.mark.parametrize(
    ("provider", "key_field", "timeout_field", "key"),
    [
        ("google_genai", "google_api_key", "timeout", "test-google"),
        ("openai", "openai_api_key", "request_timeout", "test-openai"),
        ("deepseek", "api_key", "request_timeout", "test-deepseek"),
    ],
)
def test_default_builders_use_settings_key_timeout_and_one_retry(
    fake_keys, tmp_path, provider, key_field, timeout_field, key
):
    config = load_llm_config(write_config(tmp_path / "models.yaml"))
    entry = next(m for m in config.models.values() if m.provider == provider)
    model = build_chat_model(entry, 45)
    assert getattr(model, key_field).get_secret_value() == key
    assert getattr(model, timeout_field) == 45
    assert model.max_retries == 1


def test_repository_deepseek_models_disable_thinking(fake_keys):
    # Thinking mode rejects the forced tool_choice that structured output sends.
    config = load_llm_config(REPO_CONFIG, model_overrides={"deepseek": "deepseek-v4-pro"})
    for model in (load_llm_config(REPO_CONFIG).models["deepseek"], config.models["deepseek"]):
        built = build_chat_model(model, 30)
        assert built.extra_body == {"thinking": {"type": "disabled"}}


def test_default_embedding_builder_requests_768_dimensions(fake_keys, tmp_path):
    config = load_llm_config(write_config(tmp_path / "models.yaml"))
    embeddings = build_embedding_model(config.models["gemini_embedding"], 30)
    assert embeddings.output_dimensionality == 768
    assert embeddings.google_api_key.get_secret_value() == "test-google"


def test_missing_key_is_a_config_error_naming_the_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    monkeypatch.setattr("minemydinner.llm.registry.get_settings", lambda: settings)
    config = load_llm_config(write_config(tmp_path / "models.yaml"))
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        build_chat_model(config.models["openai"], 30)


USAGE = {
    "input_tokens": 10,
    "cached_input_tokens": 0,
    "cache_write_input_tokens": 0,
    "output_tokens": 5,
}


def call_model(cfg, model):
    """An attempt that really calls the (fake) model, so a provider error surfaces."""
    model.with_structured_output(Schema, include_raw=True).invoke(MESSAGES)
    return "ok", USAGE


def test_run_task_fails_over_and_logs_both(llm_primary_fails, session):
    outcome = llm_primary_fails.run_task("planning", call_model)
    assert outcome.fallback_used is True and outcome.model_name.startswith("openai:")
    assert [c.success for c in calls(session)] == [False, True]


def test_run_task_passes_candidate_config_and_built_model(llm, primary_fake):
    seen = []

    def attempt(cfg, model):
        seen.append((cfg.model, model))
        return "ok", None

    outcome = llm.run_task("planning", attempt)
    assert seen == [("deepseek-test", primary_fake)]
    assert outcome.model_name == "deepseek:deepseek-test" and outcome.fallback_used is False


def test_run_task_without_usage_logs_no_tokens(llm, session):
    llm.run_task("planning", lambda cfg, model: ("ok", None))
    call = only_call(session)
    assert call.input_tokens is None and call.cost_estimate is None and call.success is True


def test_run_task_attempt_exception_fails_over_then_raises(llm, session):
    def attempt(cfg, model):
        raise RuntimeError("boom")

    with pytest.raises(AllModelsFailed):
        llm.run_task("planning", attempt)
    assert [c.success for c in calls(session)] == [False, False]


def test_run_task_cap_error_from_attempt_never_fails_over(llm, session):
    def attempt(cfg, model):
        raise MonthlyCapExceeded("stop")

    with pytest.raises(MonthlyCapExceeded):
        llm.run_task("planning", attempt)
    assert calls(session) == []


def test_run_task_respects_cap_for_non_essential(llm_over_cap):
    with pytest.raises(MonthlyCapExceeded):
        llm_over_cap.run_task("extraction", lambda cfg, model: ("ok", USAGE))


def test_run_task_ignores_cap_for_planning(llm_over_cap):
    assert llm_over_cap.run_task("planning", lambda cfg, model: ("ok", USAGE)).value == "ok"


def test_prepare_messages_and_restore_text_apply_hooks(llm_hooked):
    prepared = llm_hooked.prepare_messages([HumanMessage("小李 eats")])
    assert prepared[0].content == "Diner 1 eats"
    assert llm_hooked.restore_text("Diner 1 eats") == "小李 eats"


class BrokenLogFactory:
    """Reads work, but writing the call log fails."""

    def __init__(self, real):
        self.real = real

    def __call__(self):
        return self.real()

    def begin(self):
        raise OperationalError("INSERT", {}, RuntimeError("db down"))


def test_failed_log_write_keeps_the_result(make_client, session_factory, caplog):
    client = make_client()
    client.session_factory = BrokenLogFactory(session_factory)
    with caplog.at_level(logging.ERROR, logger="minemydinner.llm.client"):
        assert client.invoke_structured("extraction", MESSAGES, Schema).parsed
    assert "llm_calls" in caplog.text


def test_smoke_png_is_a_valid_solid_red_8x8():
    import struct
    import zlib

    from minemydinner.llm.smoke import solid_red_png

    png = solid_red_png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    chunks, position = {}, 8
    while position < len(png):
        length, kind = struct.unpack(">I4s", png[position : position + 8])
        data = png[position + 8 : position + 8 + length]
        (crc,) = struct.unpack(">I", png[position + 8 + length : position + 12 + length])
        assert crc == zlib.crc32(kind + data)
        chunks[kind] = data
        position += 12 + length
    assert struct.unpack(">II", chunks[b"IHDR"][:8]) == (8, 8)
    pixels = zlib.decompress(chunks[b"IDAT"])
    assert pixels == (b"\x00" + b"\xff\x00\x00" * 8) * 8


def test_smoke_flags_default_off_and_parse():
    from minemydinner.llm.smoke import parse_args

    default = parse_args([])
    assert (default.tools, default.deepseek_vision) == (False, False)
    flagged = parse_args(["--tools", "--deepseek-vision"])
    assert (flagged.tools, flagged.deepseek_vision) == (True, True)


def test_attempt_error_keeps_usage_in_the_failed_row_and_chains_the_cause(llm, session):
    from minemydinner.llm.client import AttemptError

    def attempt(cfg, model):
        raise AttemptError("x", USAGE) from ValueError("bad output")

    with pytest.raises(AllModelsFailed) as raised:
        llm.run_task("planning", attempt)
    assert isinstance(raised.value.__cause__, ValueError)
    rows = calls(session)
    assert [row.success for row in rows] == [False, False]
    assert all(row.input_tokens == 10 and row.cost_estimate > 0 for row in rows)


def test_smoke_checks_go_through_llm_client_and_log(
    tmp_path, session_factory, session, monkeypatch, capsys
):
    from langchain_core.messages import AIMessage

    from minemydinner.llm import smoke

    class ToolModel:
        def bind_tools(self, tools, **options):
            return self

        def invoke(self, messages):
            return AIMessage(
                content="red",
                tool_calls=[{"name": "get_time", "args": {"city": "Paris"}, "id": "1"}],
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )

    register_provider("fake_primary", lambda model, timeout: ToolModel())
    register_provider("fake_fallback", lambda model, timeout: ToolModel())
    path = write_config(tmp_path / "models.yaml", providers=FAKE_PROVIDERS)
    config = load_llm_config(path)
    config.tasks["smoke"] = config.tasks["extraction"]
    monkeypatch.setattr(smoke, "SessionFactory", session_factory)
    monkeypatch.setattr(smoke, "get_settings", lambda: Settings(_env_file=None))
    assert smoke.check_tools(config) is True
    assert smoke.check_deepseek_vision(config) is True
    assert [row.task for row in calls(session)] == ["smoke"] * 3
    assert "success=True" in capsys.readouterr().out


def catalog_model(**prices: str) -> ModelConfig:
    return ModelConfig(
        model="priced",
        provider="fake",
        capabilities=["tool_calling"],
        context_window=100_000,
        **prices,
    )


def test_cached_tokens_are_not_double_counted():
    model = catalog_model(
        input_usd_per_mtok="1.00", cached_input_usd_per_mtok="0.10", output_usd_per_mtok="2.00"
    )
    usage = {
        "input_tokens": 1_000_000,
        "cached_input_tokens": 600_000,
        "cache_write_input_tokens": 0,
        "output_tokens": 1_000_000,
    }
    assert _estimate_cost(model, usage) == Decimal("0.40") + Decimal("0.06") + Decimal("2.00")


def test_cache_writes_use_the_write_price():
    model = catalog_model(
        input_usd_per_mtok="0.10",
        cached_input_usd_per_mtok="0.01",
        cache_write_usd_per_mtok="0.125",
        output_usd_per_mtok="0.50",
    )
    usage = {
        "input_tokens": 1_000_000,
        "cached_input_tokens": 200_000,
        "cache_write_input_tokens": 300_000,
        "output_tokens": 0,
    }
    assert _estimate_cost(model, usage) == Decimal("0.05") + Decimal("0.0375") + Decimal("0.002")


def test_writes_without_a_write_price_use_the_input_price():
    model = catalog_model(
        input_usd_per_mtok="1.00", cached_input_usd_per_mtok="0.10", output_usd_per_mtok="2.00"
    )
    usage = {
        "input_tokens": 1_000_000,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 300_000,
        "output_tokens": 0,
    }
    assert _estimate_cost(model, usage) == Decimal("1.00")


def test_missing_cache_report_is_priced_uncached():
    assert normalize_usage({"input_tokens": 100, "output_tokens": 5}) == {
        "input_tokens": 100,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 5,
    }


def test_no_usage_normalizes_to_none():
    assert normalize_usage(None) is None


def test_langchain_cache_details_are_read():
    usage = {
        "input_tokens": 100,
        "output_tokens": 5,
        "input_token_details": {"cache_read": 60, "cache_creation": 30},
    }
    assert normalize_usage(usage) == {
        "input_tokens": 100,
        "cached_input_tokens": 60,
        "cache_write_input_tokens": 30,
        "output_tokens": 5,
    }


def test_cache_counts_never_exceed_the_total():
    usage = {
        "input_tokens": 100,
        "output_tokens": 5,
        "input_token_details": {"cache_read": 90, "cache_creation": 50},
    }
    result = normalize_usage(usage)
    assert result["cached_input_tokens"] == 90 and result["cache_write_input_tokens"] == 10


def test_llm_calls_row_stores_cached_tokens(llm, primary_fake, session):
    primary_fake.usage = {
        "input_tokens": 100,
        "output_tokens": 5,
        "total_tokens": 105,
        "input_token_details": {"cache_read": 60},
    }
    llm.invoke_structured("extraction", MESSAGES, Schema)
    row = only_call(session)
    assert (row.input_tokens, row.cached_input_tokens, row.cache_write_input_tokens) == (100, 60, 0)
    # gemini-test: input 1.0, cached 0.5, output 2.0 per million tokens.
    assert row.cost_estimate == Decimal("0.000080")


def test_embedding_rows_store_zero_cached_tokens(llm, session):
    llm.embed(["x" * 40])
    row = only_call(session)
    assert (row.cached_input_tokens, row.cache_write_input_tokens) == (0, 0)
