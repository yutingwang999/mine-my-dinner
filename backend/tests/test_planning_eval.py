"""Planning evaluation: the pure summary, the database guards, and a fake-model run.

No test here calls a real model. The end-to-end tests commit to the test database, so they
clean up after themselves and skip when the test database is not empty.
"""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from minemydinner.evaluation.planning import (
    KEPT_TABLES,
    RunOutcome,
    Scenario,
    UnsafeDatabase,
    check_expectations,
    check_shortlist_spread,
    check_test_database,
    ensure_empty,
    evaluate,
    load_library,
    load_scenarios,
    summarize,
    uncached_cost,
    UnsafeWorkingTree,
    build_summary,
    ensure_clean_tree,
    main,
    make_run_id,
    percentile,
    run_info,
    run_scenario,
    write_run,
)
from minemydinner.llm.client import LLMClient
from minemydinner.llm.config import load_llm_config
from minemydinner.llm.registry import register_embedding_provider, register_provider
from minemydinner.models import LlmCall, PlanningRun, Recipe
from minemydinner.seed import seed_all
from minemydinner.settings import Settings
from tests.agentfakes import FakeEmbeddings, ScriptedChatModel, final_proposal
from tests.fakes import write_config
from tests.planningfakes import make_recipe

MAIN_URL = "postgresql+psycopg://u:p@localhost:1/minemydinner"
TEST_URL = "postgresql+psycopg://u:p@localhost:1/minemydinner_test"


def test_summarize_rates_and_averages():
    s = summarize(
        [
            RunOutcome("a", True, True, 0, Decimal("0.01"), 900, 5),
            RunOutcome("b", False, True, 2, Decimal("0.03"), 2100, 9),
        ]
    )
    assert s == {
        "first_pass_rate": 0.5,
        "final_pass_rate": 1.0,
        "avg_retries": 1.0,
        "cost_usd": Decimal("0.04"),
        "avg_latency_ms": 1500,
        "avg_model_calls": 7.0,
        "as_expected": "2/2",
        "checks": "0/0",
        "cache_hit_rate": 0.0,
        "max_call_input_tokens": 0,
        "uncached_cost_usd": Decimal("0"),
    }


def test_summarize_reports_cache_use_and_expectations():
    s = summarize(
        [
            RunOutcome(
                "a", True, True, 0, Decimal("0.01"), 900, 5,
                input_tokens=10_000, cached_input_tokens=6_000, max_call_input_tokens=4_000,
                uncached_cost=Decimal("0.03"), as_expected=True,
            ),
            RunOutcome(
                "b", False, False, 2, Decimal("0.03"), 2100, 9,
                input_tokens=30_000, cached_input_tokens=0, max_call_input_tokens=12_000,
                uncached_cost=Decimal("0.05"), as_expected=False,
            ),
        ]
    )
    assert s["as_expected"] == "1/2"
    assert s["cache_hit_rate"] == 0.15
    assert s["max_call_input_tokens"] == 12_000
    assert s["uncached_cost_usd"] == Decimal("0.08")


def test_uncached_cost_prices_every_input_token_at_the_full_rate():
    prices = {("p", "m"): (Decimal("2"), Decimal("10"))}
    # 1,000,000 input (some cached) at $2 plus 100,000 output at $10 per million.
    assert uncached_cost([("p", "m", 1_000_000, 100_000)], prices) == Decimal("3")
    assert uncached_cost([("p", "m", None, None)], prices) == Decimal("0")
    assert uncached_cost([], prices) == Decimal("0")


# ---- expectation checks ----


def _plan(titles=(), types=(), reasons=(), actions=(), unmet=(), warnings=()):
    return {
        "recipes": [
            {"title": t, "dish_type": types[i] if i < len(types) else "vegetable dish"}
            for i, t in enumerate(titles)
        ],
        "reasons": [{"reason": r} for r in reasons],
        "next_actions": list(actions),
        "unmet": [{"code": c} for c in unmet],
        "warnings": [{"code": c} for c in warnings],
    }


def test_expectations_with_nothing_stated_only_check_the_plan_flag():
    assert check_expectations(_scenario(), _plan(["a"]), True, 3) == []
    assert check_expectations(_scenario(), _plan(), False, 3) != []
    assert check_expectations(_scenario(expect_plan=False), _plan(), False, 3) == []
    assert check_expectations(_scenario(), None, False, 3) != []


def test_expectations_check_reasons_and_next_actions():
    scenario = _scenario(
        expect_plan=False,
        expect_reasons=["not_enough_in_library"],
        expect_next_actions=["relax_split", "import_recipes"],
    )
    good = _plan(reasons=["not_enough_in_library"], actions=["relax_split", "import_recipes"])
    assert check_expectations(scenario, good, False, 1) == []
    wrong = _plan(reasons=["time_limit"], actions=["relax_split"])
    failures = check_expectations(scenario, wrong, False, 1)
    assert any("reason" in f for f in failures) and any("next action" in f for f in failures)


def test_expectations_accept_any_of_several_codes():
    scenario = _scenario(expect_plan=False, expect_any_code=["no_cookware", "H3"])
    assert check_expectations(scenario, _plan(reasons=["no_cookware"]), False, 1) == []
    assert check_expectations(scenario, _plan(unmet=["H3"]), False, 1) == []
    assert check_expectations(scenario, _plan(unmet=["H4"]), False, 1) != []


def test_expectations_check_counts_titles_warnings_and_calls():
    plan = _plan(
        ["m1", "m2", "v1"], ["meat dish", "meat dish", "vegetable dish"], warnings=["S1"]
    )
    ok = _scenario(
        expect_counts={"meat_dishes": 2, "vegetable_dishes": 1, "soups": 0},
        expect_titles_any=["v1", "zzz"],
        expect_titles_all=["m1", "m2", "v1", "extra"],
        expect_warnings=["S1"],
        max_model_calls=5,
    )
    assert check_expectations(ok, plan, True, 5) == []
    bad = _scenario(
        expect_counts={"meat_dishes": 1},
        expect_titles_any=["nope"],
        expect_titles_all=["m1"],
        expect_warnings=["S2"],
        max_model_calls=2,
    )
    assert len(check_expectations(bad, plan, True, 3)) == 5


def test_a_shortlist_spread_check_flags_a_dominated_list():
    groups = {1: frozenset({7}), 2: frozenset({7}), 3: frozenset({7}), 4: frozenset({8}), 5: None}
    assert check_shortlist_spread([1, 2, 4, 5], groups, max_same=2) == []
    assert check_shortlist_spread([1, 2, 3, 4], groups, max_same=2) != []


def test_summarize_of_nothing_is_zeros():
    s = summarize([])
    assert s["first_pass_rate"] == 0.0 and s["cost_usd"] == Decimal("0")


def test_refuses_the_main_database():
    settings = Settings(_env_file=None, database_url=MAIN_URL, test_database_url=TEST_URL)
    with pytest.raises(UnsafeDatabase):
        check_test_database(settings)


def test_refuses_when_both_urls_name_the_main_database():
    settings = Settings(_env_file=None, database_url=MAIN_URL, test_database_url=MAIN_URL)
    with pytest.raises(UnsafeDatabase):
        check_test_database(settings)


def test_accepts_the_test_database_and_never_shows_the_url():
    settings = Settings(_env_file=None, database_url=TEST_URL, test_database_url=TEST_URL)
    check_test_database(settings)
    with pytest.raises(UnsafeDatabase) as error:
        check_test_database(
            Settings(_env_file=None, database_url=MAIN_URL, test_database_url=TEST_URL)
        )
    assert "localhost" not in str(error.value) and "u:p" not in str(error.value)


def test_refuses_a_test_database_that_is_not_empty(session):
    seed_all(session)
    session.flush()
    with pytest.raises(UnsafeDatabase, match="not empty"):
        ensure_empty(session)


def test_refuses_a_test_database_with_recipes(session):
    make_recipe(session, "Leftover", lines=())
    with pytest.raises(UnsafeDatabase, match="not empty"):
        ensure_empty(session)


def test_loaders_read_the_scenario_schema(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "scenarios": [
                    {
                        "name": "x",
                        "request": "dinner",
                        "locale": "en",
                        "inventory": {"鸡蛋": 2, "小白菜": "plenty"},
                        "cookware": ["炒锅"],
                        "rules": [{"type": "allergy", "allergen": "egg"}],
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    [scenario] = load_scenarios(path)
    assert scenario.name == "x" and scenario.expect_plan is True and scenario.cookware == ["炒锅"]
    path.write_text(yaml.safe_dump({"scenarios": [{"name": "y"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_scenarios(path)


# ---- fake-model end to end, on the test database ----


def _config_with_planning_eval(tmp_path, embedding_provider=None):
    providers = {"deepseek": "eval_primary", "openai": "eval_fallback"}
    if embedding_provider:
        providers["gemini_embedding"] = embedding_provider
    path = write_config(tmp_path / "models.yaml", providers=providers)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["tasks"]["planning_eval"] = {**document["tasks"]["planning"], "essential": False}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


class TitleModel(ScriptedChatModel):
    """Proposes the recipe with the given title, found in the database when asked."""

    def __init__(self, factory, title, **kwargs):
        super().__init__(
            replies=[final_proposal([1])],
            structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]},
            **kwargs,
        )
        self.factory = factory
        self.title = title

    def invoke(self, messages, config=None, **kwargs):
        with self.factory() as session:
            recipe_id = session.scalar(select(Recipe.id).where(Recipe.title == self.title))
        self.replies = [final_proposal([recipe_id])]
        return super().invoke(messages, config, **kwargs)


@pytest.fixture
def eval_db(engine):
    """A committing session factory on the test database; leaves it as it found it."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        try:
            ensure_empty(session)
        except UnsafeDatabase:
            pytest.skip("The test database is not empty")
        calls = session.scalar(select(func.coalesce(func.max(LlmCall.id), 0)))
        runs = session.scalar(select(func.coalesce(func.max(PlanningRun.id), 0)))
    yield factory
    with factory() as session:
        session.query(LlmCall).filter(LlmCall.id > calls).delete()
        session.query(PlanningRun).filter(PlanningRun.id > runs).delete()
        session.commit()


def _client(tmp_path, factory, model, embedding_provider=None):
    register_provider("eval_primary", lambda m, timeout: model)
    register_provider("eval_fallback", lambda m, timeout: model)
    config = load_llm_config(_config_with_planning_eval(tmp_path, embedding_provider))
    return LLMClient(config, factory, Decimal("20"))


LIBRARY = [
    {
        "title": "清炒小白菜",
        "dish_type": "vegetable dish",
        "servings": 1,
        "cookware": ["炒锅"],
        "lines": [{"name": "小白菜", "role": "main"}],
    }
]


def _scenario(name="one", **changes) -> Scenario:
    values = {
        "name": name,
        "request": "今晚一个人吃，一个菜。",
        "locale": "zh",
        "inventory": {"小白菜": "plenty"},
        "cookware": ["炒锅"],
        "rules": [],
    }
    return Scenario(**{**values, **changes})


def _assert_clean(factory):
    with factory() as session:
        tables = set(inspect(session.get_bind()).get_table_names()) - set(KEPT_TABLES)
        for table in sorted(tables):
            count = session.scalar(text(f'SELECT count(*) FROM "{table}"'))
            assert count == 0, f"{table} still has rows"


def test_runs_a_scenario_with_a_fake_model_and_cleans_up(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    outcomes = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [_scenario()], InMemorySaver()
    )
    [outcome] = outcomes
    assert (outcome.first_passed, outcome.final_passed, outcome.retries) == (True, True, 0)
    # The route call, the parse call, and the planner call.
    assert outcome.model_calls == 3
    with eval_db() as session:
        tasks = set(session.scalars(select(LlmCall.task).where(LlmCall.id > 0)))
    assert "planning_eval" in tasks
    _assert_clean(eval_db)


def test_a_failing_scenario_is_recorded_and_the_run_continues(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜", error=RuntimeError("down"))
    outcomes = evaluate(
        eval_db,
        _client(tmp_path, eval_db, model),
        LIBRARY,
        [_scenario("a"), _scenario("b")],
        InMemorySaver(),
    )
    assert [o.scenario for o in outcomes] == ["a", "b"]
    assert not any(o.final_passed for o in outcomes)
    assert all("failed" in o.note for o in outcomes)
    _assert_clean(eval_db)


def test_cleanup_runs_when_the_library_is_bad(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    bad = [{**LIBRARY[0], "lines": [{"name": "no such ingredient", "role": "main"}]}]
    with pytest.raises(Exception):
        evaluate(eval_db, _client(tmp_path, eval_db, model), bad, [_scenario()], InMemorySaver())
    _assert_clean(eval_db)


def test_load_library_links_lines_by_name(session):
    seed_all(session)
    session.flush()
    recipes = load_library(
        session,
        [
            {
                **LIBRARY[0],
                "lines": [
                    {"name": "小白菜", "role": "main"},
                    {"name": "秘制腌料", "role": "secondary", "linked": False},
                ],
            }
        ],
    )
    [recipe] = recipes
    linked = {line.name: line.ingredient_id is not None for line in recipe.ingredients}
    assert linked == {"小白菜": True, "秘制腌料": False}


def test_an_infeasible_scenario_stops_before_the_planner(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    scenario = _scenario(
        "empty",
        request="今晚一个人吃，一个菜，只用冰箱里的。",
        inventory={},
        expect_plan=False,
        expect_reasons=["empty_inventory"],
        expect_next_actions=["switch_to_shopping", "update_inventory"],
        max_model_calls=3,
    )
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    # The route call and the parse call only: the planner never ran.
    assert outcome.model_calls == 2
    assert outcome.reasons == ["empty_inventory"]
    assert "switch_to_shopping" in outcome.next_actions
    assert outcome.as_expected, outcome.failures
    assert outcome.max_call_input_tokens >= 0 and outcome.uncached_cost >= 0
    _assert_clean(eval_db)


def test_the_no_shortlist_switch_still_runs_a_scenario(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    [outcome] = evaluate(
        eval_db,
        _client(tmp_path, eval_db, model),
        LIBRARY,
        [_scenario()],
        InMemorySaver(),
        use_shortlist=False,
    )
    assert outcome.final_passed and outcome.as_expected
    _assert_clean(eval_db)


def test_load_library_reads_exact_piece_amounts(session):
    seed_all(session)
    session.flush()
    [recipe] = load_library(
        session,
        [
            {
                **LIBRARY[0],
                "title": "蛋",
                "lines": [{"name": "鸡蛋", "role": "main", "amount": 3}, {"name": "盐"}],
            }
        ],
    )
    egg, salt = recipe.ingredients
    assert (egg.amount, egg.unit, egg.amount_type) == (3, "piece", "count")
    assert (salt.amount, salt.amount_type) == (None, "vague")


def test_expectations_check_assumed_defaults():
    scenario = _scenario(expect_assumed=["dishes", "soups"])
    plan = {**_plan(["a"]), "defaults_used": ["headcount", "dishes", "soups", "mode"]}
    assert check_expectations(scenario, plan, True, 3) == []
    short = {**_plan(["a"]), "defaults_used": ["dishes"]}
    assert check_expectations(scenario, short, True, 3) != []


def test_check_only_runs_stay_out_of_the_summary():
    s = summarize(
        [
            RunOutcome("a", True, True, 0, Decimal("0.01"), 900, 5),
            RunOutcome(
                "spread", True, True, 0, Decimal("0"), 0, 0, check_only=True, as_expected=False
            ),
        ]
    )
    assert s["first_pass_rate"] == 1.0 and s["avg_model_calls"] == 5.0
    assert s["avg_latency_ms"] == 900 and s["as_expected"] == "1/1"
    assert s["checks"] == "0/1"
    assert summarize([RunOutcome("a", True, True, 0, Decimal("0"), 0, 1)])["checks"] == "0/0"


def test_a_check_only_scenario_needs_a_client():
    with pytest.raises(ValueError, match="client"):
        run_scenario(None, None, None, _scenario(shortlist_spread=3))


# ---- machine-checked expectations (fix round 2) ----

TITLES = ("青椒肉丝", "清炒小白菜", "番茄炒蛋")


def _check(scenario, plan, passed=True, calls=3, first=True, reply="", titles=TITLES):
    return check_expectations(
        scenario, plan, passed, calls, first_passed=first, reply=reply, library_titles=titles
    )


def test_title_set_checks():
    plan = _plan(["青椒肉丝", "清炒小白菜"])
    assert _check(_scenario(expect_titles_exact=["清炒小白菜", "青椒肉丝"]), plan) == []
    assert _check(_scenario(expect_titles_exact=["青椒肉丝"]), plan) != []
    assert _check(_scenario(expect_titles_include=["青椒肉丝"]), plan) == []
    assert _check(_scenario(expect_titles_include=["番茄炒蛋"]), plan) != []
    assert _check(_scenario(expect_titles_none=["番茄炒蛋"]), plan) == []
    assert _check(_scenario(expect_titles_none=["青椒肉丝"]), plan) != []


def test_not_assumed_first_pass_and_missing():
    plan = {**_plan(["a"]), "defaults_used": ["headcount"], "missing": {"1": ["盐"]}}
    assert _check(_scenario(expect_not_assumed=["dishes", "soups"]), plan) == []
    assert _check(_scenario(expect_not_assumed=["headcount"]), plan) != []
    assert _check(_scenario(expect_first_pass=True), plan, first=False) != []
    assert _check(_scenario(expect_first_pass=True), plan) == []
    assert _check(_scenario(expect_missing_nonempty=True), plan) == []
    assert _check(_scenario(expect_missing_nonempty=True), {**plan, "missing": {}}) != []


def test_reason_detail_and_s1_and_left_out():
    plan = {
        **_plan(),
        "reasons": [
            {"reason": "named_ingredient", "category": None, "needed": None,
             "available": None, "ingredient": "鸡蛋", "absent_from_stock": True},
            {"reason": "not_enough_dishes", "category": "meat_dishes", "needed": 1,
             "available": 0},
        ],
        "warnings": [{"code": "S1", "detail": {"name": "鸡蛋", "needed": 6, "have": 4}}],
        "left_out_unknown_allergens": ["香煎鸡腿"],
    }
    scenario = _scenario(
        expect_plan=False,
        expect_reason_detail=[
            {"reason": "named_ingredient", "ingredient": "鸡蛋", "absent_from_stock": True},
            {"reason": "not_enough_dishes", "category": "meat_dishes", "needed": 1,
             "available": 0},
        ],
        expect_s1=[{"name": "鸡蛋", "needed": 6, "have": 4}],
        expect_left_out_include=["香煎鸡腿"],
    )
    assert _check(scenario, plan, passed=False) == []
    bad = _scenario(
        expect_plan=False,
        expect_reason_detail=[{"reason": "not_enough_dishes", "available": 5}],
        expect_s1=[{"name": "鸡蛋", "needed": 7, "have": 4}],
        expect_left_out_include=["别的菜"],
    )
    assert len(_check(bad, plan, passed=False)) == 3


def test_locale_reply_check_ignores_recipe_titles():
    zh_titles = ("青椒肉丝",)
    en = _scenario(expect_locale_reply="en")
    assert _check(en, _plan(["a"]), reply="Pork with green pepper: 青椒肉丝 tonight.", titles=zh_titles) == []
    assert _check(en, _plan(["a"]), reply="青椒肉丝 and 今晚吃", titles=zh_titles) != []
    zh = _scenario(expect_locale_reply="zh")
    assert _check(zh, _plan(["a"]), reply="今晚吃青椒肉丝", titles=zh_titles) == []
    assert _check(zh, _plan(["a"]), reply="Dinner: 青椒肉丝", titles=zh_titles) != []

# ---- run folders ----

REPLY = "SECRET-MODEL-REPLY 今晚吃饭"
RUNS_PREFIX = "backend/eval_data/planning/runs/"


def _outcomes():
    return [
        RunOutcome(
            "a", True, True, 0, Decimal("0.0123"), 900, 5, input_tokens=100,
            cached_input_tokens=40, cache_write_input_tokens=10, max_call_input_tokens=60,
            uncached_cost=Decimal("0.02"), reasons=["time_limit"],
            next_actions=["drop_time_limit"], thread_id="t-1", planning_run_id=7,
            titles=["青椒肉丝"], detail="recipes: 青椒肉丝", embedding_calls=2,
            wall_ms=1500, call_latencies=[100, 200, 200, 400],
            record={
                "planning_run": {"events": [{"response": {"content": REPLY}}]},
                "llm_calls": [{"id": 1, "task": "planning_eval", "served": None}],
            },
        ),
        RunOutcome(
            "b", False, False, 1, Decimal("0.01"), 2100, 3, as_expected=False, failures=["x"],
            input_tokens=50, thread_id="t-2", planning_run_id=8, expected_plan=False,
            wall_ms=2500, call_latencies=[700, 1400],
        ),
        RunOutcome(
            "spread", True, True, 0, Decimal("0"), 0, 0, check_only=True, record={},
        ),
    ]


def _info(tmp_path, **kwargs):
    scenarios = tmp_path / "scenarios_demo.yaml"
    library = tmp_path / "lib.yaml"
    scenarios.write_text("scenarios: []", encoding="utf-8")
    library.write_text("dishes: []", encoding="utf-8")
    client = LLMClient(load_llm_config(_config_with_planning_eval(tmp_path)), None, Decimal("1"))
    return run_info(
        scenarios, library, client, no_shortlist=kwargs.get("no_shortlist", False),
        with_embeddings=kwargs.get("with_embeddings", False), git_commit="abc123",
        started_at="2026-11-15T10:00:00+00:00", finished_at="2026-11-15T10:05:00+00:00",
    )


def test_run_id_format():
    from datetime import datetime, timezone

    when = datetime(2026, 11, 15, 4, 0, 0, tzinfo=timezone.utc)
    assert make_run_id(when, Path("x/scenarios_baseline.yaml"), False, False) == (
        "20261115T040000Z-baseline-shortlist-no-vectors"
    )
    assert make_run_id(when, Path("scenarios_plan2a.yaml"), True, True) == (
        "20261115T040000Z-plan2a-no-shortlist-vectors"
    )
    assert make_run_id(when, Path("custom.yaml"), False, True).endswith("-custom-shortlist-vectors")


def test_percentiles_use_nearest_rank():
    values = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    assert percentile(values, 50) == 500 and percentile(values, 95) == 1000
    assert percentile([7], 50) == 7 and percentile([], 95) == 0
    assert percentile([30, 10, 20], 50) == 20


def _write(tmp_path, embeddings=0):
    import json

    info = _info(tmp_path, no_shortlist=True, with_embeddings=embeddings > 0)
    summary = build_summary("RID", info, _outcomes(), embeddings)
    folder = write_run(tmp_path / "runs", "RID", summary, _outcomes())
    return folder, json.loads((folder / "summary.json").read_text(encoding="utf-8"))


def test_run_folder_layout_and_summary(tmp_path):
    folder, data = _write(tmp_path, embeddings=3)
    assert folder == tmp_path / "runs" / "RID"
    assert {p.name for p in folder.iterdir()} == {
        "summary.json", "results.jsonl", "tools.json", "prompts"
    }
    assert set(data) == {
        "run_id", "started_at", "finished_at", "conditions", "timing", "metrics", "scenarios"
    }
    cond = data["conditions"]
    assert set(cond) == {
        "git_commit", "scenarios_file", "library_file", "prompt_version", "prompt_versions",
        "prompt_files", "tools_version", "flags", "constants", "models", "planning_model",
    }
    assert cond["git_commit"] == "abc123" and len(cond["scenarios_file"]["sha256"]) == 64
    assert cond["flags"] == {"no_shortlist": True, "with_embeddings": True}
    assert cond["constants"]["MODEL_CALL_LIMIT"] == 6
    model = cond["models"]["planning_eval"]["candidates"][0]
    assert {"provider", "model", "input_usd_per_mtok", "cached_input_usd_per_mtok",
            "output_usd_per_mtok"} <= set(model)
    assert "prices_updated" in cond["models"]
    assert data["timing"]["total_seconds"] == 300
    metrics = data["metrics"]
    assert set(metrics) == {"basis", "checks", "groups"} and metrics["checks"] == "1/1"
    for word in ("model_time_ms", "wall_ms", "per_call"):
        assert word in metrics["basis"]
    assert set(metrics["groups"]) == {"all", "expect_plan", "expect_stop"}
    m = metrics["groups"]["all"]
    assert set(m) == {"scenarios", "outcomes", "calls", "tokens", "cost", "latency", "notes"}
    assert m["scenarios"] == 2 and m["outcomes"]["as_expected"] == "1/2"
    assert m["calls"]["embedding_calls"] == {"library": 3, "scenarios": 2, "total": 5}
    assert m["calls"]["max_model_calls"] == 5 and m["calls"]["model_calls"] == 8
    assert m["tokens"]["input"] == 150 and m["tokens"]["written"] == 10
    assert m["tokens"]["cache_hit_rate"] == pytest.approx(40 / 150)
    assert m["tokens"]["avg_per_scenario"] == {"input": 75, "cached": 20, "written": 5}
    assert m["cost"]["cache_aware_usd"] == "0.0223"
    assert Decimal(m["cost"]["avg_per_scenario"]["cache_aware_usd"]) == Decimal("0.01115")
    assert Decimal(m["cost"]["avg_per_scenario"]["uncached_usd"]) == Decimal("0.01")
    lat = m["latency"]
    assert lat["wall_ms"] == {"avg": 2000, "p50": 1500, "p95": 2500, "max": 2500}
    assert lat["model_time_ms"] == {"avg": 1500, "p50": 900, "p95": 2100}
    assert lat["per_call_ms"] == {"avg": 500, "p50": 200, "p95": 1400}
    plan = metrics["groups"]["expect_plan"]
    assert plan["scenarios"] == 1 and plan["outcomes"]["as_expected"] == "1/1"
    assert plan["latency"]["per_call_ms"] == {"avg": 225, "p50": 200, "p95": 400}
    assert plan["latency"]["wall_ms"]["max"] == 1500 and "library" not in plan["calls"]["embedding_calls"]
    stop = metrics["groups"]["expect_stop"]
    assert stop["scenarios"] == 1 and stop["outcomes"]["as_expected"] == "0/1"
    assert stop["latency"]["model_time_ms"]["avg"] == 2100
    first = data["scenarios"][0]
    assert first["name"] == "a" and first["planning_run_id"] == 7 and first["cost"] == "0.0123"
    assert first["titles"] == ["青椒肉丝"] and first["check_only"] is False
    assert first["wall_ms"] == 1500 and first["model_time_ms"] == 900
    assert "latency_ms" not in first and "call_latencies" not in first
    assert data["scenarios"][2]["thread_id"] is None and data["scenarios"][2]["check_only"]
    assert "record" not in first and "detail" not in first


def test_summary_holds_no_model_text_but_results_do(tmp_path):
    import json

    folder, _ = _write(tmp_path)
    summary_text = (folder / "summary.json").read_text(encoding="utf-8")
    lines = (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and REPLY in lines[0] and REPLY not in summary_text
    first = json.loads(lines[0])
    assert first["name"] == "a"
    assert first["planning_run"]["events"][0]["response"]["content"] == REPLY
    assert first["llm_calls"][0]["task"] == "planning_eval"
    assert "青椒肉丝" in summary_text  # titles are kept, unescaped
    for text in (summary_text, "\n".join(lines)):
        assert TEST_URL not in text and "postgresql" not in text and "api_key" not in text.lower()


def test_tools_and_prompts_are_saved(tmp_path):
    import hashlib
    import json

    from minemydinner.agent.planner import PlanProposal
    from minemydinner.agent.request import PROMPT_VERSION, PROMPT_VERSIONS, prompt_files
    from minemydinner.planning.tools import PLANNER_TOOLS

    folder, data = _write(tmp_path)
    tools = json.loads((folder / "tools.json").read_text(encoding="utf-8"))
    assert [t["name"] for t in tools["tools"]] == [t.name for t in PLANNER_TOOLS]
    for saved, tool in zip(tools["tools"], PLANNER_TOOLS):
        assert saved["description"] == tool.description
        assert saved["args_schema"] == tool.tool_call_schema.model_json_schema()
    assert tools["final_answer"] == {
        "name": "PlanProposal", "schema": PlanProposal.model_json_schema()
    }
    copied = {p.name: p.read_bytes() for p in (folder / "prompts").iterdir()}
    in_use = {p.name: p.read_bytes() for p in prompt_files()}
    # route, parse, planner, and revise (plan 3b-1).
    assert copied == in_use and len(copied) == 4
    cond = data["conditions"]
    assert cond["prompt_version"] == PROMPT_VERSION
    assert cond["prompt_versions"] == PROMPT_VERSIONS
    assert cond["prompt_files"] == {
        name: hashlib.sha256(content).hexdigest() for name, content in in_use.items()
    }


def test_a_dirty_tree_is_refused_and_runs_are_excluded(monkeypatch):
    import minemydinner.evaluation.planning as module

    def status(lines):
        monkeypatch.setattr(module, "_git", lambda *a: lines)

    status("")
    assert ensure_clean_tree() is None
    status(f"?? {RUNS_PREFIX}\n M {RUNS_PREFIX}x/summary.json\n")
    assert ensure_clean_tree() is None
    status(f" M backend/minemydinner/x.py\n?? {RUNS_PREFIX}y/\n")
    with pytest.raises(UnsafeWorkingTree, match="uncommitted"):
        ensure_clean_tree()
    status(" M backend/eval_data/planning/library.yaml\n")
    with pytest.raises(UnsafeWorkingTree):
        ensure_clean_tree()
    status(None)  # git unavailable
    with pytest.raises(UnsafeWorkingTree, match="git"):
        ensure_clean_tree()


def test_the_conditions_record_prices_and_the_embedding_task(tmp_path):
    conditions = _info(tmp_path)["conditions"]
    assert set(conditions["models"]) >= {"planning_eval", "prices_updated"}
    assert "embeddings" in conditions["models"]


def test_with_embeddings_embeds_each_recipe_and_counts_the_calls(tmp_path, eval_db):
    embedder = FakeEmbeddings()
    register_embedding_provider("eval_embed", lambda m, timeout: embedder)
    model = TitleModel(eval_db, "清炒小白菜")
    client = _client(tmp_path, eval_db, model, embedding_provider="eval_embed")
    library = [LIBRARY[0], {**LIBRARY[0], "title": "炒小白菜二号"}]
    stats = {}
    [outcome] = evaluate(
        eval_db, client, library, [_scenario()], InMemorySaver(), with_embeddings=True,
        stats=stats,
    )
    assert len(embedder.embedded) == 2 and stats["library_embedding_calls"] == 2
    assert stats["library_embeddings_failed"] == 0
    assert outcome.final_passed and outcome.embedding_calls == 0
    _assert_clean(eval_db)


def test_the_planner_record_and_calls_reach_the_outcome(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [_scenario()], InMemorySaver()
    )
    run = outcome.record["planning_run"]
    assert outcome.record["planning_runs"] == [run]
    assert run["thread_id"] == outcome.thread_id and run["events"]
    assert {c["task"] for c in outcome.record["llm_calls"]} == {"planning_eval"}
    assert len(outcome.record["llm_calls"]) == outcome.model_calls
    assert outcome.cache_write_input_tokens >= 0


def test_a_failing_save_never_hides_the_run_error(tmp_path, monkeypatch, capsys):
    import contextlib

    import minemydinner.evaluation.planning as module

    class Boom(Exception):
        pass

    settings = Settings(_env_file=None, database_url=TEST_URL, test_database_url=TEST_URL)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "ensure_clean_tree", lambda: None)
    monkeypatch.setattr(module, "_git", lambda *a: "abc\n")
    monkeypatch.setattr(module, "read_library", lambda path: [])
    monkeypatch.setattr(module, "load_scenarios", lambda path: [_scenario()])
    monkeypatch.setattr(
        module, "create_engine", lambda url: type("E", (), {"dispose": lambda s: None})()
    )
    monkeypatch.setattr(module, "sessionmaker", lambda **kw: None)
    monkeypatch.setattr(module, "load_llm_config", lambda **kw: None)
    monkeypatch.setattr(module, "LLMClient", lambda *a, **kw: None)
    monkeypatch.setattr(module, "open_checkpointer", lambda url: contextlib.nullcontext(None))

    def run_then_fail(*args, **kwargs):
        args[5](RunOutcome("a", True, True, 0, Decimal("0"), 0, 1))
        raise Boom("run failed")

    def bad_save(*args, **kwargs):
        raise KeyError("secret-looking detail")

    monkeypatch.setattr(module, "evaluate", run_then_fail)
    monkeypatch.setattr(module, "run_info", bad_save)
    with pytest.raises(Boom):
        main(["--scenarios", str(tmp_path / "s.yaml"), "--library", str(tmp_path / "l.yaml")])
    err = capsys.readouterr().err
    assert "KeyError" in err and "secret-looking" not in err


def test_a_dirty_tree_stops_main_before_anything_runs(tmp_path, monkeypatch, capsys):
    import minemydinner.evaluation.planning as module

    monkeypatch.setattr(module, "_git", lambda *a: " M backend/x.py\n")
    called = []
    monkeypatch.setattr(module, "get_settings", lambda: called.append(1))
    code = main(["--scenarios", str(tmp_path / "s.yaml"), "--library", str(tmp_path / "l.yaml")])
    assert code == 2 and not called and "uncommitted" in capsys.readouterr().err


def test_an_empty_group_is_all_zeros(tmp_path):
    summary = build_summary("RID", _info(tmp_path), [], 0)
    stop = summary["metrics"]["groups"]["expect_stop"]
    assert stop["scenarios"] == 0 and stop["latency"]["wall_ms"]["max"] == 0
    assert stop["tokens"]["avg_per_scenario"]["input"] == 0


def test_wall_ms_and_call_latencies_are_recorded(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [_scenario()], InMemorySaver()
    )
    assert outcome.wall_ms > 0
    assert len(outcome.call_latencies) == outcome.model_calls
    assert sum(outcome.call_latencies) == outcome.latency_ms


# ---- design 5.11: count ranges, the spec, and a follow-up message ----


def test_count_range_expectation():
    plan = _plan(["m1", "m2", "v1"], ["meat dish", "meat dish", "vegetable dish"])
    ok = _scenario(expect_count_range={"meat_dishes": [2, 3], "vegetable_dishes": [0, 1]})
    assert _check(ok, plan) == []
    bad = _scenario(expect_count_range={"meat_dishes": [3, 3], "vegetable_dishes": [0, 0]})
    (failure,) = _check(bad, plan)
    assert "meat_dishes" in failure and "vegetable_dishes" in failure


def test_spec_expectation():
    plan = {**_plan(["a"]), "spec": {"all_use": [], "prefer_use": ["鸡蛋"], "meat_dishes": None}}
    ok = _scenario(expect_spec={"all_use": [], "prefer_use": ["鸡蛋"], "meat_dishes": None})
    assert _check(ok, plan) == []
    assert _check(_scenario(expect_spec={"all_use": ["鸡蛋"]}), plan) != []


def test_first_reasons_are_checked_on_the_first_plan():
    scenario = _scenario(follow_up="荤素都行", expect_first_reasons=["not_enough_dishes"])
    first = _plan(reasons=["not_enough_dishes"])
    assert check_expectations(scenario, _plan(["a"]), True, 5, first_plan=first) == []
    assert check_expectations(scenario, _plan(["a"]), True, 5, first_plan=_plan()) != []


def test_loader_reads_the_counts_scenarios():
    path = Path(__file__).resolve().parents[1] / "eval_data" / "planning" / "scenarios_counts.yaml"
    scenarios = load_scenarios(path)
    names = {s.name for s in scenarios}
    assert len(scenarios) == 10 and len(names) == 10
    for scenario in scenarios:
        assert scenario.max_model_calls is not None
        if scenario.follow_up:
            assert scenario.expect_first_reasons
        elif scenario.expect_plan:
            assert scenario.max_model_calls == 5
        else:
            assert scenario.expect_reason_detail and scenario.expect_next_actions


def test_a_follow_up_message_runs_on_the_same_thread(tmp_path, eval_db):
    """The button scenario: the first message stops early (no meat dish), the follow-up
    drops the split, and the second plan is the one judged."""
    model = TitleModel(eval_db, "清炒小白菜")
    model.structured["ParsedRequest"] = [
        {"meat_dishes": 1, "vegetable_dishes": 1, "mode": "fridge_only"},
        {"dishes": 1, "mode": "fridge_only"},
    ]
    scenario = _scenario(
        "button",
        request="一荤一素，只用冰箱里的",
        follow_up="荤素都行",
        expect_first_reasons=["not_enough_in_library"],
        expect_spec={"meat_dishes": None, "vegetable_dishes": None, "dishes": 1},
        expect_counts={"dishes": 1},
        max_model_calls=7,
    )
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    assert outcome.as_expected, outcome.failures
    assert outcome.final_passed
    # Route and parse for the first message; route, parse, and planner for the second.
    assert outcome.model_calls == 5
    # The second parse read the whole conversation, ending with the button's message.
    parse_calls = [m for m in model.sent if "extract the details" in str(m[0].content)]
    texts = [str(m.content) for m in parse_calls[-1][1:]]
    assert texts[0] == "一荤一素，只用冰箱里的" and texts[-1] == "荤素都行"
    # Both turns' runs are saved in order; the single-run field is the judged (last) one.
    first_run, second_run = outcome.record["planning_runs"]
    assert first_run["id"] < second_run["id"]
    assert outcome.record["planning_run"] == second_run
    assert outcome.planning_run_id == second_run["id"]
    (feasibility,) = [e for e in first_run["events"] if e["kind"] == "feasibility"]
    assert [s["reason"] for s in feasibility["shortfalls"]] == ["not_enough_in_library"]
    assert [r["reason"] for r in first_run["final_plan"]["reasons"]] == ["not_enough_in_library"]
    assert first_run["final_passed"] is False and second_run["final_passed"] is True
    _assert_clean(eval_db)


def test_counts_scenarios_stock_gives_the_stated_feasibility(session):
    """The fridge-only stock of scenarios_counts.yaml makes the dishes its comments state,
    so each scenario's expectation follows from the request, not from the model."""
    from minemydinner.evaluation.planning import read_library, reset_scenario_state
    from minemydinner.models import Diner
    from minemydinner.planning.context import MealConstraints, load_planning_context
    from minemydinner.planning.feasibility import Shortfall, check_feasibility
    from minemydinner.planning.validate import PlanSpec

    folder = Path(__file__).resolve().parents[1] / "eval_data" / "planning"
    seed_all(session)
    session.flush()
    load_library(session, read_library(folder / "library_extended.yaml"))
    by_name = {s.name: s for s in load_scenarios(folder / "scenarios_counts.yaml")}

    def check(name, **spec):
        reset_scenario_state(session, by_name[name])
        self_id = session.scalar(select(Diner.id).where(Diner.is_self))
        ctx = load_planning_context(session, MealConstraints(diner_ids=(self_id,)))
        return check_feasibility(session, ctx, PlanSpec(mode="fridge_only", soups=0, **spec))

    name = "eight_dishes_at_least_two_meat_fridge"
    assert check(name, dishes=8, meat_dishes=2, meat_bound="at_least") == []
    assert check(name, dishes=8) == []
    assert check("eight_dishes_two_meat_fridge", dishes=8, meat_dishes=2) == [
        Shortfall("not_enough_dishes", "vegetable_dishes", 6, 5)
    ]
    found = check(
        "three_dishes_at_most_one_vegetable_fridge",
        dishes=3,
        vegetable_dishes=1,
        vegetable_bound="at_most",
    )
    assert found == [Shortfall("not_enough_dishes", "meat_dishes", 2, 1, minimum=True)]
    first = check("relax_split_button", dishes=2, meat_dishes=1, vegetable_dishes=1)
    assert [(s.reason, s.category) for s in first] == [("not_enough_dishes", "meat_dishes")]
    assert check("relax_split_button", dishes=2) == []
    first = check("relax_named_button", dishes=1, all_use=["鸡蛋"])
    assert [(s.reason, s.absent_from_stock) for s in first] == [("named_ingredient", True)]
    assert check("relax_named_button", dishes=1, prefer_use=["鸡蛋"]) == []


# ---- note checks, planning model, notes review (chat page redesign, Task 4) ----

_PLAN = ["糖醋小排"]
_LIBRARY = ["糖醋小排", "胡萝卜炒鸡蛋", "清炒小白菜"]


def _flags(note, locale="zh", plan=_PLAN, library=_LIBRARY):
    from minemydinner.evaluation.planning import note_flags

    return note_flags(note, plan, library, locale)


@pytest.mark.parametrize(
    "note, locale, expected",
    [
        ("糖醋小排最费时，可以先腌上。", "zh", []),
        ("可以把糖醋小排换成胡萝卜炒鸡蛋", "zh", ["outside_dish"]),
        ("这一餐安排了 3 道荤菜", "zh", ["counts_dishes"]),
        ("两荤一素，很均衡", "zh", ["counts_dishes"]),
        ("实际为荤 4 道", "zh", ["counts_dishes"]),
        ("Start the ribs first. It has 4 dishes.", "en", ["counts_dishes", "multi_sentence"]),
        ("Start the ribs first. Then the eggs.", "en", ["multi_sentence"]),
        ("Start the ribs first, they take 1.5 hours.", "en", []),
        ("荤素一起下锅更入味", "zh", []),
        ("荤素十分搭配，先腌糖醋小排", "zh", []),
        ("先做第一道菜糖醋小排", "zh", []),
        ("糖醋小排是最费时的一道，先腌上", "zh", []),
        ("Start the ribs first; this dish 20 minutes in the oven", "en", []),
        ("第三十道不算", "zh", []),
        ("这一道菜最费时，先做它。", "zh", []),
        ("其中一道要先腌。", "zh", []),
        ("每一道都不难。", "zh", []),
        ("最费时的一道是排骨。", "zh", []),
        ("一荤一素很清爽。", "zh", ["counts_dishes"]),
        ("这一餐有十一道菜。", "zh", ["counts_dishes"]),
        ("先腌糖醋小排。", "zh", []),
        ("先腌糖醋小排。再炒菜！", "zh", ["multi_sentence"]),
        ("先腌糖醋小排，再炒菜", "en", ["wrong_locale"]),
        ("Start the ribs first.", "zh", ["wrong_locale"]),
    ],
)
def test_note_flags(note, locale, expected):
    assert _flags(note, locale) == expected


def test_a_plan_title_inside_a_longer_library_title_is_not_an_outside_dish():
    assert _flags("先做番茄炒蛋。", plan=["番茄炒蛋"], library=["番茄炒蛋", "炒蛋"]) == []


def test_the_shown_note_leaves_out_the_closest_attempt_text():
    from minemydinner.evaluation.planning import shown_model_note

    closest = "这是我能找到的最接近的方案，有些规则没有满足，详见下方。"
    assert shown_model_note(closest, "zh", has_plan=True) == ""
    assert shown_model_note(closest + "\n\n先腌排骨。", "zh", has_plan=True) == "先腌排骨。"
    assert shown_model_note("先腌排骨。", "zh", has_plan=True) == "先腌排骨。"
    assert shown_model_note("fixed stop text", "en", has_plan=False) == ""


def test_a_scenario_records_its_note_and_flags(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [_scenario()], InMemorySaver()
    )
    assert outcome.reply_note == "Here is your dinner."
    assert outcome.note_checked and outcome.note_flags == ["wrong_locale"]


def _note_outcome(name, flags, checked=True):
    return RunOutcome(
        name, True, True, 0, Decimal("0"), 0, 1, titles=["糖醋小排"],
        reply_note="先腌糖醋小排。" if checked else "", note_flags=flags, note_checked=checked,
    )


def test_note_metrics_are_per_group_and_in_the_summary_without_the_text(tmp_path):
    import json

    outcomes = [
        _note_outcome("a", []),
        _note_outcome("b", ["counts_dishes", "multi_sentence"]),
        _note_outcome("c", ["counts_dishes"]),
        _note_outcome("d", [], checked=False),
        _note_outcome("e", ["multi_sentence"]),
    ]
    summary = build_summary("RID", _info(tmp_path), outcomes, 0)
    notes = summary["metrics"]["groups"]["all"]["notes"]
    # multi_sentence is counted in flags but not in flagged_rate: "e" is not a flagged note.
    assert notes["checked"] == 4 and notes["flagged_rate"] == pytest.approx(2 / 4)
    assert notes["flags"] == {
        "outside_dish": 0, "counts_dishes": 2, "multi_sentence": 2, "wrong_locale": 0
    }
    assert [s["note_flags"] for s in summary["scenarios"]][:3] == [
        [], ["counts_dishes", "multi_sentence"], ["counts_dishes"]
    ]
    assert "先腌糖醋小排" not in json.dumps(summary, ensure_ascii=False, default=str)
    empty = summary["metrics"]["groups"]["expect_stop"]["notes"]
    assert empty == {
        "checked": 0,
        "flags": {"outside_dish": 0, "counts_dishes": 0, "multi_sentence": 0, "wrong_locale": 0},
        "flagged_rate": 0.0,
    }
    folder = write_run(tmp_path / "runs", "RID", summary, outcomes)
    assert "先腌糖醋小排" in (folder / "results.jsonl").read_text(encoding="utf-8")


def test_run_id_gains_the_model_only_when_the_flag_is_given():
    from datetime import datetime, timezone

    when = datetime(2026, 11, 15, 4, 0, 0, tzinfo=timezone.utc)
    path = Path("scenarios_plan2a.yaml")
    assert make_run_id(when, path, False, False) == "20261115T040000Z-plan2a-shortlist-no-vectors"
    assert make_run_id(when, path, False, False, "gpt-6-luna") == (
        "20261115T040000Z-plan2a-shortlist-no-vectors-gpt-6-luna"
    )


def test_the_planning_model_override_pins_the_task_without_a_fallback(tmp_path):
    from minemydinner.llm.config import ConfigError, pin_task_model, tool_calling_models

    path = _config_with_planning_eval(tmp_path)
    assert tool_calling_models(path) == ["deepseek-test", "openai-test"]
    config = load_llm_config(path)
    pinned = pin_task_model(config, "planning_eval", "openai-test", path)
    assert pinned.tasks["planning_eval"].candidates == ["openai-test"]
    assert pinned.models["openai-test"].model == "openai-test"
    assert config.tasks["planning_eval"].candidates == ["deepseek", "openai"]  # untouched
    assert pinned.tasks["planning"] == config.tasks["planning"]
    for bad in ("gemini-test", "embedding-test", "nope"):
        with pytest.raises(ConfigError, match="deepseek-test"):
            pin_task_model(config, "planning_eval", bad, path)


def test_the_run_records_the_planning_model(tmp_path):
    from minemydinner.llm.config import pin_task_model

    assert _info(tmp_path)["conditions"]["planning_model"] == "deepseek-test"
    path = _config_with_planning_eval(tmp_path)
    config = pin_task_model(load_llm_config(path), "planning_eval", "openai-test", path)
    client = LLMClient(config, None, Decimal("1"))
    scenarios = tmp_path / "s.yaml"
    library = tmp_path / "l.yaml"
    scenarios.write_text("scenarios: []", encoding="utf-8")
    library.write_text("dishes: []", encoding="utf-8")
    info = run_info(
        scenarios, library, client, False, False, None, "2026-11-15T10:00:00+00:00",
        "2026-11-15T10:05:00+00:00",
    )
    assert info["conditions"]["planning_model"] == "openai-test"
    models = info["conditions"]["models"]["planning_eval"]["candidates"]
    assert [m["model"] for m in models] == ["openai-test"]


def test_main_runs_on_the_named_planning_model(tmp_path, monkeypatch):
    """Drives main with fakes only (no model call, no database): the flag pins the task, the
    run id ends with the model, and summary.json records it."""
    import contextlib
    import json

    import minemydinner.evaluation.planning as module

    settings = Settings(_env_file=None, database_url=TEST_URL, test_database_url=TEST_URL)
    scenarios = tmp_path / "scenarios_demo.yaml"
    library = tmp_path / "library.yaml"
    scenarios.write_text("scenarios: []", encoding="utf-8")
    library.write_text("dishes: []", encoding="utf-8")
    seen = {}

    def fake_evaluate(factory, client, *args, **kwargs):
        seen["task"] = client.config.tasks[module.EVAL_TASK]
        return [RunOutcome("a", True, True, 0, Decimal("0"), 0, 1)]

    def fake_evaluate_with_callback(factory, client, library_, scenarios_, saver, on_outcome, **kw):
        outcomes = fake_evaluate(factory, client)
        for outcome in outcomes:
            on_outcome(outcome)
        return outcomes

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "ensure_clean_tree", lambda: None)
    monkeypatch.setattr(module, "_git", lambda *a: "abc\n")
    monkeypatch.setattr(module, "read_library", lambda path: [])
    monkeypatch.setattr(module, "load_scenarios", lambda path: [_scenario()])
    monkeypatch.setattr(
        module, "create_engine", lambda url: type("E", (), {"dispose": lambda s: None})()
    )
    monkeypatch.setattr(module, "sessionmaker", lambda **kw: None)
    monkeypatch.setattr(module, "open_checkpointer", lambda url: contextlib.nullcontext(None))
    monkeypatch.setattr(module, "evaluate", fake_evaluate_with_callback)
    code = main([
        "--scenarios", str(scenarios), "--library", str(library),
        "--results-dir", str(tmp_path / "runs"), "--planning-model", "gpt-6-luna",
    ])
    assert code == 0
    assert seen["task"].candidates == ["gpt-6-luna"]
    [folder] = (tmp_path / "runs").iterdir()
    assert folder.name.endswith("-demo-shortlist-no-vectors-gpt-6-luna")
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["run_id"] == folder.name
    assert summary["conditions"]["planning_model"] == "gpt-6-luna"


def test_a_broken_model_config_stops_the_cli_with_an_error(tmp_path, monkeypatch, capsys):
    import minemydinner.evaluation.planning as module
    from minemydinner.llm.config import ConfigError

    def broken():
        raise ConfigError("bad models.yaml")

    monkeypatch.setattr(module, "tool_calling_models", broken)
    code = main([
        "--scenarios", str(tmp_path / "s.yaml"), "--library", str(tmp_path / "l.yaml"),
        "--planning-model", "gpt-6-luna",
    ])
    assert code == 2 and "bad models.yaml" in capsys.readouterr().err


def test_the_cli_rejects_an_unknown_planning_model(tmp_path, monkeypatch, capsys):
    import minemydinner.evaluation.planning as module

    monkeypatch.setattr(module, "get_settings", lambda: pytest.fail("must stop first"))
    code = main([
        "--scenarios", str(tmp_path / "s.yaml"), "--library", str(tmp_path / "l.yaml"),
        "--planning-model", "nope-9",
    ])
    err = capsys.readouterr().err
    assert code == 2 and "nope-9" in err and "gpt-6-luna" in err and "deepseek-flash" in err
    assert "gemini-embedding-001" not in err


def _fixture_run(root, run_id, model, rows):
    import json

    folder = root / run_id
    folder.mkdir(parents=True)
    summary = {"run_id": run_id, "conditions": {"planning_model": model}}
    (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    (folder / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def test_notes_review_writes_one_row_per_plan_scenario(tmp_path):
    import csv

    from minemydinner.evaluation.notes_review import HEADER, main as review

    first = _fixture_run(tmp_path, "R1", "gpt-6-luna", [
        {"name": "one", "titles": ["糖醋小排", "清炒小白菜"], "reply_note": "先腌糖醋小排。",
         "note_flags": [], "note_checked": True},
        {"name": "stop", "titles": [], "reply_note": "", "note_flags": [], "note_checked": False},
        {"name": "two", "titles": ["糖醋小排"], "reply_note": "两荤一素。",
         "note_flags": ["counts_dishes", "multi_sentence"], "note_checked": True},
    ])
    second = _fixture_run(tmp_path, "R2", "deepseek-flash", [
        {"name": "one", "titles": ["糖醋小排"], "reply_note": "Start now.",
         "note_flags": ["wrong_locale"], "note_checked": True},
    ])
    out = tmp_path / "review.csv"
    assert review(["--runs", str(first), str(second), "--out", str(out)]) == 0
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == HEADER == [
        "model", "run_id", "scenario", "plan_titles", "note", "flags", "quality",
        "should_not_appear", "comment",
    ]
    assert len(rows) == 4
    assert rows[1] == [
        "gpt-6-luna", "R1", "one", "糖醋小排 / 清炒小白菜", "先腌糖醋小排。", "", "", "", ""
    ]
    assert rows[2][2] == "two" and rows[2][5] == "counts_dishes,multi_sentence"
    assert rows[3][:3] == ["deepseek-flash", "R2", "one"] and rows[3][6:] == ["", "", ""]


# ---- plan 2b: diners, ask-first answers, privacy checks (Task 11) ----

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval_data" / "planning"


class NotedTitleModel(TitleModel):
    """TitleModel whose plan note is given."""

    def __init__(self, factory, title, note, **kwargs):
        super().__init__(factory, title, **kwargs)
        self.note = note

    def invoke(self, messages, config=None, **kwargs):
        with self.factory() as session:
            recipe_id = session.scalar(select(Recipe.id).where(Recipe.title == self.title))
        self.replies = [final_proposal([recipe_id], note=self.note)]
        return ScriptedChatModel.invoke(self, messages, config, **kwargs)


def _hooked_client(tmp_path, factory, model):
    from minemydinner.agent.labels import replace_nicknames, restore_labels

    register_provider("eval_primary", lambda m, timeout: model)
    register_provider("eval_fallback", lambda m, timeout: model)
    config = load_llm_config(_config_with_planning_eval(tmp_path))
    return LLMClient(
        config,
        factory,
        Decimal("20"),
        pre_hooks=(replace_nicknames,),
        post_hooks=(restore_labels,),
    )


FRIENDS = [
    {"name": "小王", "rules": [{"type": "allergy", "allergen": "peanut"}]},
    {"name": "老李", "rules": [{"type": "dietary restriction", "ingredient": "牛肉"}]},
]


def _parse_calls(model):
    return [m for m in model.sent if "extract the details" in str(m[0].content)]


def test_runner_answers_card_accept(tmp_path, eval_db):
    """A saved friend named in the request is preselected on the card; the runner accepts
    the card, the friend's rule applies, and the other friend's does not."""
    model = TitleModel(eval_db, "清炒小白菜")
    model.structured["ParsedRequest"] = [
        {"headcount": 3, "dishes": 1, "guests": [{"name": "Alex", "allergens": ["egg"]}]}
    ]
    scenario = _scenario(
        "card",
        request="今晚我和小王，还有 Alex 一起吃，一个菜。Alex 对鸡蛋过敏。",
        friends=FRIENDS,
        expect_card={
            "preselected": ["me", "小王"],
            "others": 0,
            "nudge": False,
            "guests": [{"name": "Alex", "allergens": ["egg"]}],
        },
        expect_rules_applied=[
            {"who": "小王", "allergen": "peanut"},
            {"who": "guest", "allergen": "egg"},
        ],
        expect_rules_not_applied=[{"who": "老李", "ingredient": "牛肉"}],
    )
    [outcome] = evaluate(
        eval_db, _hooked_client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    assert outcome.as_expected, outcome.failures
    assert outcome.card_shown and outcome.cards_shown == 1
    assert outcome.card["kind"] == "diners"
    assert outcome.final_passed
    # The parse saw the friend as a label, never by nickname.
    parse_texts = "".join(str(m.content) for m in _parse_calls(model)[0][1:])
    assert "小王" not in parse_texts and "Diner 2" in parse_texts
    _assert_clean(eval_db)


def test_runner_answers_card_with_an_explicit_answer(tmp_path, eval_db):
    """diners_answer overrides the selection and the guests; the expectations judge the
    planning context's rules."""
    model = TitleModel(eval_db, "清炒小白菜")
    model.structured["ParsedRequest"] = [{"headcount": 3, "dishes": 1}]
    scenario = _scenario(
        "explicit",
        request="今晚三个人吃，一个菜。",
        friends=FRIENDS,
        diners_answer={
            "selected": ["me", "老李"],
            "guests": [{"name": "Sam", "rules": [{"type": "allergy", "allergen": "fish"}]}],
            "others": 0,
        },
        expect_card={"preselected": ["me"], "others": 2},
        expect_rules_applied=[
            {"who": "老李", "ingredient": "牛肉"},
            {"who": "guest", "allergen": "fish"},
        ],
        expect_rules_not_applied=[{"who": "小王", "allergen": "peanut"}],
    )
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    assert outcome.as_expected, outcome.failures
    _assert_clean(eval_db)


def test_runner_answers_ask_first_questions(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    model.structured["ParsedRequest"] = [{}, {"dishes": 1, "mode": "shopping"}]
    scenario = _scenario(
        "ask",
        request="今晚我一个人吃。",
        request_style="ask_first",
        answers=["一个，可以买菜"],
        expect_card=False,
        expect_spec={"dishes": 1, "mode": "shopping"},
    )
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    assert outcome.as_expected, outcome.failures
    assert outcome.note == "" and outcome.final_passed
    # The second parse read the answer after the question.
    parse_calls = _parse_calls(model)
    assert len(parse_calls) == 2
    assert str(parse_calls[-1][-1].content) == "一个，可以买菜"
    _assert_clean(eval_db)


def test_runner_stops_at_a_question_with_no_answer_left(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    model.structured["ParsedRequest"] = [{}]
    scenario = _scenario("ask", request="今晚我一个人吃。", request_style="ask_first")
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    assert outcome.note == "stopped at a question: question"
    assert not outcome.as_expected
    _assert_clean(eval_db)


def test_runner_flags_nickname_sent(tmp_path, eval_db):
    """Without the nickname pre-hook the friend's nickname reaches the model, and the
    planning record shows it: a mismatch, even though everything else is as expected."""
    model = TitleModel(eval_db, "清炒小白菜")
    model.structured["ParsedRequest"] = [{"headcount": 2, "dishes": 1}]
    scenario = _scenario("leak", request="我和小王两个人吃，一个菜。", friends=FRIENDS[:1])
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    assert outcome.final_passed
    assert any(f.startswith("nickname sent") and "小王" in f for f in outcome.failures)
    assert not outcome.as_expected
    _assert_clean(eval_db)


def test_runner_flags_label_shown(tmp_path, eval_db):
    model = NotedTitleModel(eval_db, "清炒小白菜", "Diner 2 会喜欢这道菜。")
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [_scenario()], InMemorySaver()
    )
    assert outcome.final_passed
    assert any(f.startswith("label shown") for f in outcome.failures)
    assert not outcome.as_expected
    _assert_clean(eval_db)


def test_runner_expect_card_false_mismatch(tmp_path, eval_db):
    model = TitleModel(eval_db, "清炒小白菜")
    model.structured["ParsedRequest"] = [{"headcount": 2, "dishes": 1}]
    scenario = _scenario("no_card", request="我们两个人吃，一个菜。", expect_card=False)
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), LIBRARY, [scenario], InMemorySaver()
    )
    # The runner still answers the card and plans; the card itself is the mismatch.
    assert outcome.final_passed and outcome.card_shown
    assert any("card" in f for f in outcome.failures)
    _assert_clean(eval_db)


def test_between_turns_confirms_allergens_before_the_follow_up(tmp_path, eval_db):
    """The plan-again case: the first turn leaves out the dish with an unlinked line; the
    runner confirms the line's allergens on the database; the follow-up plans it."""
    model = TitleModel(eval_db, "香煎鸡腿")
    model.structured["ParsedRequest"] = [{"dishes": 1, "mode": "shopping"}]
    library = [
        {
            "title": "香煎鸡腿",
            "dish_type": "meat dish",
            "servings": 1,
            "cookware": ["平底锅"],
            "lines": [
                {"name": "鸡腿", "role": "main"},
                {"name": "秘制腌料", "role": "secondary", "linked": False},
            ],
        }
    ]
    scenario = _scenario(
        "again",
        request="今晚我一个人吃，一个菜，可以买菜。",
        inventory={"鸡腿": 2},
        cookware=["平底锅"],
        rules=[{"type": "allergy", "allergen": "peanut"}],
        follow_up="按同样的要求重新规划",
        between_turns=[{"confirm_allergens": {"name": "秘制腌料", "allergens": []}}],
        expect_titles_exact=["香煎鸡腿"],
    )
    [outcome] = evaluate(
        eval_db, _client(tmp_path, eval_db, model), library, [scenario], InMemorySaver()
    )
    assert outcome.as_expected, outcome.failures
    first_run, second_run = outcome.record["planning_runs"]
    assert "香煎鸡腿" in first_run["final_plan"]["left_out_unknown_allergens"]
    _assert_clean(eval_db)


def test_reset_recreates_friends(session):
    from minemydinner.evaluation.planning import reset_scenario_state
    from minemydinner.models import DietaryRule, Diner

    seed_all(session)
    session.flush()
    self_id = session.scalar(select(Diner.id).where(Diner.is_self))
    old = Diner(display_name="Old friend", is_self=False)
    session.add(old)
    session.flush()
    session.add(DietaryRule(diner_id=old.id, rule_type="allergy", allergen="egg"))
    session.commit()
    reset_scenario_state(
        session,
        _scenario(friends=FRIENDS, rules=[{"type": "allergy", "allergen": "sesame"}]),
    )
    diners = session.scalars(select(Diner).order_by(Diner.id)).all()
    assert [d.id for d in diners if d.is_self] == [self_id]
    friends = {d.display_name: d for d in diners if not d.is_self}
    assert set(friends) == {"小王", "老李"}
    rules = session.scalars(select(DietaryRule)).all()
    by_diner = {(r.diner_id, r.rule_type, r.allergen) for r in rules}
    assert (self_id, "allergy", "sesame") in by_diner
    assert (friends["小王"].id, "allergy", "peanut") in by_diner
    [beef] = [r for r in rules if r.diner_id == friends["老李"].id]
    assert beef.rule_type == "dietary restriction" and beef.ingredient.name_zh == "牛肉"
    assert len(rules) == 3
    # A second reset with no friends leaves only the user.
    reset_scenario_state(session, _scenario())
    assert [d.is_self for d in session.scalars(select(Diner))] == [True]


def test_reset_sets_the_request_style(session):
    from minemydinner.agent.request import get_request_style
    from minemydinner.evaluation.planning import reset_scenario_state

    seed_all(session)
    session.flush()
    reset_scenario_state(session, _scenario(request_style="ask_first"))
    assert get_request_style(session) == "ask_first"
    reset_scenario_state(session, _scenario())
    assert get_request_style(session) == "plan_then_adjust"


def test_heldout_file_loads():
    """The held-out and development sets parse into Scenario. The held-out content must not
    be shown, so a failure reports only the file and, for an unknown key, the key."""
    problems = []
    for name in (
        "scenarios_heldout.yaml", "scenarios_heldout_core.yaml", "scenarios_diners.yaml"
    ):
        problem = None
        try:
            scenarios = load_scenarios(EVAL_DIR / name)
        except Exception as error:  # noqa: BLE001 - reported without its message
            cause = error.__cause__
            problem = type(error).__name__
            if isinstance(cause, TypeError) and "keyword argument" in str(cause):
                problem += ": " + str(cause).rsplit("keyword argument", 1)[1].strip()
            elif cause is not None:
                problem += f" from {type(cause).__name__}"
            scenarios = []
        if problem:
            problems.append(f"{name} does not load ({problem})")
        elif not scenarios or not all(isinstance(s, Scenario) for s in scenarios):
            problems.append(f"{name} holds no scenarios")
    if problems:
        pytest.fail("; ".join(problems), pytrace=False)


def test_the_loader_refuses_unknown_diner_keys(tmp_path):
    base = {"name": "x", "request": "r", "locale": "zh", "inventory": {}, "cookware": []}
    bad = [
        {"request_style": "sometimes"},
        {"diners_answer": "reject"},
        {"diners_answer": {"chosen": ["me"]}},
        {"expect_card": {"rows": 2}},
        {"between_turns": [{"confirm_allergens": {"name": "x", "allergens": []}}]},
        {"between_turns": [{"delete_friend": "x"}], "follow_up": "again"},
    ]
    path = tmp_path / "s.yaml"
    for changes in bad:
        path.write_text(
            yaml.safe_dump({"scenarios": [{**base, **changes}]}, allow_unicode=True),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_scenarios(path)


def test_the_development_set_fits_its_library(session):
    """Every development scenario's stock, cookware, rules and friends exist in the
    dictionary, and every expected title is a library dish."""
    from minemydinner.evaluation.planning import read_library, reset_scenario_state

    library = read_library(EVAL_DIR / "library_diners.yaml")
    titles = {dish["title"] for dish in library}
    seed_all(session)
    session.flush()
    load_library(session, library)
    scenarios = load_scenarios(EVAL_DIR / "scenarios_diners.yaml")
    assert len({s.name for s in scenarios}) == len(scenarios)
    for scenario in scenarios:
        assert scenario.max_model_calls is not None, scenario.name
        reset_scenario_state(session, scenario)
        named = [
            *scenario.expect_titles_none, *scenario.expect_titles_include,
            *scenario.expect_titles_exact, *scenario.expect_titles_any,
            *scenario.expect_left_out_include,
        ]
        assert set(named) <= titles, scenario.name


# ---- pure diner checks ----

CARD = {
    "kind": "diners",
    "headcount": 3,
    "rows": [
        {"diner_id": 1, "name": None, "is_self": True, "selected": True, "rules": [],
         "proposed": [{"rule_type": "dietary restriction", "allergen": None,
                       "ingredient_id": 40, "ingredient_name": "羊肉", "text": None,
                       "by_wording": False}],
         "diet_words": []},
        {"diner_id": 2, "name": "小王", "is_self": False, "selected": True, "rules": [],
         "proposed": [], "diet_words": []},
        {"diner_id": 3, "name": "老李", "is_self": False, "selected": False, "rules": [],
         "proposed": [], "diet_words": []},
    ],
    "guests": [
        {"name": "Alex", "diet_words": ["vegetarian"], "proposed": [
            {"rule_type": "allergy", "allergen": "peanut", "ingredient_id": None,
             "ingredient_name": None, "text": None, "by_wording": False},
            {"rule_type": "dislike", "allergen": None, "ingredient_id": 41,
             "ingredient_name": "coriander", "text": None, "by_wording": False},
        ]},
    ],
    "others": 0,
    "nudge": False,
}
NAMES = {40: {"羊肉", "lamb"}, 41: {"香菜", "coriander"}}


def _card_check(expect, cards=(CARD,)):
    from minemydinner.evaluation.planning import card_failures

    return card_failures(expect, list(cards), NAMES)


def test_card_expectations():
    assert _card_check(None) == [] and _card_check(None, cards=()) == []
    assert _card_check(False, cards=()) == []
    assert _card_check(False) != []
    assert _card_check(True) == [] and _card_check(True, cards=()) != []
    assert _card_check({"preselected": ["Me", "小王"]}, cards=()) != []
    good = {
        "preselected": ["me", "小王"],
        "others": 0,
        "nudge": False,
        "diet_words": True,
        "guests": [{"name": "alex", "allergens": ["peanut"],
                    "avoid": [{"name": "香菜", "kind": "dislike"}], "diet_words": True}],
        "self_proposed": [{"ingredient": "羊肉"}],
    }
    assert _card_check(good) == []
    assert _card_check({"guests": [{"avoid": [{"name": "coriander"}]}]}) == []
    bad = [
        {"preselected": ["me"]},
        {"others": 1},
        {"nudge": True},
        {"diet_words": False},
        {"guests": [{"name": "Sam"}]},
        {"guests": [{"name": "Alex", "allergens": ["egg"]}]},
        {"guests": [{"avoid": [{"name": "香菜", "kind": "restriction"}]}]},
        {"guests": [{"name": "Alex"}, {"name": "Alex"}]},
        {"self_proposed": [{"allergen": "egg"}]},
    ]
    for expect in bad:
        assert len(_card_check(expect)) == 1, expect


def test_rule_expectations():
    from minemydinner.evaluation.planning import rule_failures

    rules = [
        {"diner_key": "diner:1", "rule_type": "allergy", "allergen": "egg",
         "ingredient_id": None, "text": None},
        {"diner_key": "diner:2", "rule_type": "dislike", "allergen": None,
         "ingredient_id": 40, "text": None},
        {"diner_key": "guest:1", "rule_type": "dietary restriction", "allergen": None,
         "ingredient_id": None, "text": "芒果"},
    ]
    who = {"diner:1": "me", "diner:2": "小王"}
    applied = [
        {"who": "me", "allergen": "egg"},
        {"who": "小王", "ingredient": "lamb"},
        {"who": "guest", "ingredient": "芒果"},
    ]
    assert rule_failures(applied, [], rules, who, NAMES) == []
    not_applied = [{"who": "小王", "allergen": "egg"}, {"who": "guest", "allergen": "egg"}]
    assert rule_failures([], not_applied, rules, who, NAMES) == []
    assert len(rule_failures([{"who": "老李", "allergen": "egg"}], [], rules, who, NAMES)) == 1
    assert len(rule_failures([], [{"who": "me", "allergen": "egg"}], rules, who, NAMES)) == 1


def test_privacy_checks():
    from minemydinner.evaluation.planning import label_failures, nickname_failures

    events = [
        {"kind": "model_call", "step": "route",
         "messages": [{"type": "human", "content": "我和Diner 2吃鱼香肉丝"}]},
        {"kind": "tool_call", "args": {"query": "小王"}},
    ]
    # A food-like nickname inside a food word is not naming the friend.
    terms = frozenset({"鱼香肉丝"})
    assert nickname_failures([events], ["小王", "鱼"], terms) == []
    leaked = [{"kind": "model_call", "step": "parse",
               "messages": [{"type": "human", "content": [{"text": "和 Alex 吃"}]}]}]
    [failure] = nickname_failures([events, leaked], ["alex"], terms)
    assert failure.startswith("nickname sent") and "parse" in failure
    assert label_failures(["今晚吃清炒小白菜。"]) == []
    assert len(label_failures(["和Diner 2一起吃", "diner  3 likes it"])) == 1
    assert label_failures(["Dinner 2 is ready"]) == []
    assert len(label_failures(["Diner2 会喜欢"])) == 1


def test_nickname_check_skips_system_prompts_and_record_fields():
    """System prompts are fixed text the pre-hook leaves as written (the parse prompt's
    examples hold names such as "Alex" and "我妈"); record fields are not model text."""
    from minemydinner.evaluation.planning import nickname_failures

    in_system = [{"kind": "model_call", "step": "parse", "messages": [
        {"type": "system", "content": "Example: 我和 Alex 吃饭，我妈不吃辣"},
        {"type": "human", "content": "今晚一个人吃"},
        {"type": "tool", "content": "{}", "name": "Alex", "tool_call_id": "Alex"},
    ]}]
    assert nickname_failures([in_system], ["Alex", "我妈"], frozenset()) == []
    in_human = [{"kind": "model_call", "step": "parse", "messages": [
        {"type": "system", "content": "fixed"},
        {"type": "human", "content": "我和我妈吃饭"},
    ]}]
    [failure] = nickname_failures([in_human], ["Alex", "我妈"], frozenset())
    assert "我妈" in failure and "Alex" not in failure
    in_args = [{"kind": "model_call", "step": "planner", "messages": [
        {"type": "ai", "content": "", "tool_calls": [
            {"name": "search", "args": {"query": "Alex 爱吃的菜"}, "id": "c1"}]},
    ]}]
    assert len(nickname_failures([in_args], ["Alex"], frozenset())) == 1


def test_reset_undoes_an_allergen_confirmation(engine, session):
    """A scenario after a confirm sees the line unknown again and the created entry gone."""
    from minemydinner.evaluation.planning import reset_scenario_state, run_between_turns
    from minemydinner.models import Ingredient, RecipeIngredient

    seed_all(session)
    session.flush()
    [recipe] = load_library(
        session,
        [{**LIBRARY[0], "lines": [{"name": "小白菜", "role": "main"},
                                  {"name": "秘制腌料", "linked": False}]}],
    )
    session.commit()
    seeds = session.scalar(select(func.count()).select_from(Ingredient))

    def factory():
        return _SessionProxy(session)

    run_between_turns(factory, [{"confirm_allergens": {"name": "秘制腌料", "allergens": []}}])
    line = session.scalars(
        select(RecipeIngredient).where(RecipeIngredient.name == "秘制腌料")
    ).one()
    session.refresh(line)
    assert line.ingredient_id is not None
    reset_scenario_state(session, _scenario())
    session.refresh(line)
    assert line.ingredient_id is None
    assert session.scalar(select(func.count()).select_from(Ingredient)) == seeds
    assert session.scalar(
        select(func.count()).select_from(RecipeIngredient)
        .where(RecipeIngredient.recipe_id == recipe.id, RecipeIngredient.ingredient_id.is_not(None))
    ) == 1
    # A name the dictionary already owns cannot be confirmed (it could not be undone).
    with pytest.raises(ValueError):
        run_between_turns(factory, [{"confirm_allergens": {"name": "小白菜", "allergens": []}}])


class _SessionProxy:
    """The test session as a context manager that does not close it (for a factory)."""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


def test_the_heldout_sets_fit_the_library(session):
    """Every dictionary lookup a held-out scenario needs (stock, cookware, rules, friends'
    rules, diners_answer guests' rules, card and rule expectations' ingredients) and every
    expected title exists. Failures report only the file, scenario index and key."""
    from minemydinner.evaluation.planning import read_library
    from minemydinner.models import Cookware, Ingredient

    library = read_library(EVAL_DIR / "library_diners.yaml")
    titles = {dish["title"] for dish in library}
    seed_all(session)
    session.flush()
    load_library(session, library)

    def known(model, name):
        return session.scalar(select(model.id).where(model.name_zh == name)) is not None

    def rule_ok(rule):
        return not rule.get("ingredient") or known(Ingredient, rule["ingredient"])

    problems = []
    for name in ("scenarios_heldout.yaml", "scenarios_heldout_core.yaml"):
        for index, s in enumerate(load_scenarios(EVAL_DIR / name)):
            def bad(key):
                problems.append(f"{name}[{index}].{key}")

            if not all(known(Ingredient, item) for item in s.inventory):
                bad("inventory")
            if not all(known(Cookware, item) for item in s.cookware):
                bad("cookware")
            if not all(rule_ok(rule) for rule in s.rules):
                bad("rules")
            if not all(rule_ok(r) for f in s.friends for r in f.get("rules") or []):
                bad("friends")
            answer = s.diners_answer if isinstance(s.diners_answer, dict) else {}
            if not all(rule_ok(r) for g in answer.get("guests") or [] for r in g.get("rules") or []):
                bad("diners_answer")
            wanted = [*s.expect_rules_applied, *s.expect_rules_not_applied]
            if isinstance(s.expect_card, dict):
                wanted += s.expect_card.get("self_proposed") or []
            if not all(rule_ok(item) for item in wanted):
                bad("expected ingredients")
            for key in ("expect_titles_none", "expect_titles_include", "expect_titles_exact",
                        "expect_titles_any", "expect_titles_all", "expect_left_out_include"):
                if not set(getattr(s, key)) <= titles:
                    bad(key)
    if problems:
        pytest.fail("; ".join(problems), pytrace=False)
