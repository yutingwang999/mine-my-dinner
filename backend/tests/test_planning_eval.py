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
        "git_commit", "scenarios_file", "library_file", "prompt_version", "tools_version",
        "flags", "constants", "models",
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
    assert set(m) == {"scenarios", "outcomes", "calls", "tokens", "cost", "latency"}
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
    import json

    from minemydinner.agent.planner import PlanProposal
    from minemydinner.agent.request import PROMPT_DIR
    from minemydinner.planning.tools import PLANNER_TOOLS

    folder, _ = _write(tmp_path)
    tools = json.loads((folder / "tools.json").read_text(encoding="utf-8"))
    assert [t["name"] for t in tools["tools"]] == [t.name for t in PLANNER_TOOLS]
    for saved, tool in zip(tools["tools"], PLANNER_TOOLS):
        assert saved["description"] == tool.description
        assert saved["args_schema"] == tool.tool_call_schema.model_json_schema()
    assert tools["final_answer"] == {
        "name": "PlanProposal", "schema": PlanProposal.model_json_schema()
    }
    copied = {p.name: p.read_bytes() for p in (folder / "prompts").iterdir()}
    assert copied == {p.name: p.read_bytes() for p in PROMPT_DIR.glob("*.md")} and copied


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
