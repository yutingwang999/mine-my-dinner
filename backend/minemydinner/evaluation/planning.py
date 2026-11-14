"""Minimal planning evaluation: fictional scenarios through the planning graph.

Runs only against the test database, which must be empty. It seeds the dictionary, loads a
fictional recipe library, runs each scenario on a fresh thread with the real planning models
(task `planning_eval`, counted and capped), prints the results, and empties the app tables.
"""

import argparse
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.agent.checkpoint import open_checkpointer
from minemydinner.agent.graph import GraphDeps, build_graph
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import set_request_style
from minemydinner.llm.client import LLMClient
from minemydinner.llm.config import load_llm_config
from minemydinner.models import (
    Cookware,
    CookwareOwned,
    DietaryRule,
    Diner,
    Ingredient,
    Inventory,
    LlmCall,
    PlanningRun,
    Recipe,
    RecipeCookware,
    RecipeIngredient,
)
from minemydinner.seed import seed_all
from minemydinner.settings import Settings, get_settings

EVAL_TASK = "planning_eval"
# The tables the cleanup keeps: the migration marker, the cost records, and the planning
# records (all of which the evaluation reads), and the checkpointer's migration marker.
KEPT_TABLES = ("alembic_version", "llm_calls", "planning_runs", "checkpoint_migrations")


class UnsafeDatabase(Exception):
    pass


class InputError(Exception):
    pass


@dataclass(frozen=True)
class RunOutcome:
    scenario: str
    first_passed: bool
    final_passed: bool
    retries: int
    cost: Decimal
    latency_ms: int
    model_calls: int
    note: str = ""
    detail: str = ""
    expected_plan: bool = True


@dataclass(frozen=True)
class Scenario:
    name: str
    request: str
    locale: str
    inventory: dict[str, Any]
    cookware: list[str]
    rules: list[dict[str, Any]] = field(default_factory=list)
    expect: str = ""
    # False for a scenario that is meant to end without a full plan.
    expect_plan: bool = True


def summarize(runs: list[RunOutcome]) -> dict[str, Any]:
    """Rates and averages over the runs. Pure."""
    count = len(runs)
    if count == 0:
        return {
            "first_pass_rate": 0.0,
            "final_pass_rate": 0.0,
            "avg_retries": 0.0,
            "cost_usd": Decimal("0"),
            "avg_latency_ms": 0,
            "avg_model_calls": 0.0,
        }
    return {
        "first_pass_rate": sum(r.first_passed for r in runs) / count,
        "final_pass_rate": sum(r.final_passed for r in runs) / count,
        "avg_retries": sum(r.retries for r in runs) / count,
        "cost_usd": sum((r.cost for r in runs), Decimal("0")),
        "avg_latency_ms": round(sum(r.latency_ms for r in runs) / count),
        "avg_model_calls": sum(r.model_calls for r in runs) / count,
    }


# ---- database guards ----


def check_test_database(settings: Settings) -> None:
    """Refuse unless DATABASE_URL is the test database. Messages never show a URL."""
    main_default = make_url(Settings.model_fields["database_url"].default)
    current = make_url(settings.database_url)
    if settings.database_url != settings.test_database_url:
        raise UnsafeDatabase("DATABASE_URL is not the test database; refusing to run")
    if current == main_default or current.database == main_default.database:
        raise UnsafeDatabase("DATABASE_URL is the main database; refusing to run")


def ensure_empty(session: Session) -> None:
    if session.scalar(select(func.count()).select_from(Recipe)) or session.scalar(
        select(func.count()).select_from(Ingredient)
    ):
        raise UnsafeDatabase("The test database is not empty; refusing to run")


def clear_app_tables(factory: sessionmaker[Session]) -> None:
    """Truncate every table except the kept ones. This also clears checkpoint rows and
    app_preferences, so the test suite's empty-database assumption holds again."""
    with factory() as session:
        tables = sorted(set(inspect(session.get_bind()).get_table_names()) - set(KEPT_TABLES))
        if tables:
            names = ", ".join(f'"{name}"' for name in tables)
            session.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        session.commit()


# ---- inputs ----


def _read_yaml(path: Path, key: str) -> list[dict[str, Any]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise InputError(f"Cannot read {path}: {error}") from error
    items = document.get(key) if isinstance(document, dict) else None
    if not isinstance(items, list) or not items:
        raise InputError(f"{path} needs a non-empty '{key}' list")
    return items


def read_library(path: Path) -> list[dict[str, Any]]:
    return _read_yaml(path, "dishes")


def load_scenarios(path: Path) -> list[Scenario]:
    scenarios = []
    for item in _read_yaml(path, "scenarios"):
        try:
            scenario = Scenario(**item)
        except TypeError as error:
            raise ValueError(f"Bad scenario {item.get('name', '?')}: {error}") from error
        if scenario.locale not in ("zh", "en"):
            raise ValueError(f"Scenario {scenario.name}: locale must be zh or en")
        scenarios.append(scenario)
    return scenarios


def load_library(session: Session, dishes: list[dict[str, Any]]) -> list[Recipe]:
    """Add the library's recipes. Lines link to the dictionary by name unless `linked` is
    false; an unknown name is an error."""
    recipes = []
    for dish in dishes:
        recipe = Recipe(
            lang="zh",
            title=dish["title"],
            dish_type=dish["dish_type"],
            time_min=dish.get("time_min"),
            time_source=dish.get("time_source"),
            servings=dish.get("servings"),
            source_type="manual",
        )
        for line in dish["lines"]:
            ingredient_id = None
            if line.get("linked", True):
                ingredient_id = _one(session, Ingredient, line["name"]).id
            recipe.ingredients.append(
                RecipeIngredient(
                    raw=line["name"],
                    name=line["name"],
                    ingredient_id=ingredient_id,
                    amount_type="vague",
                    role=line.get("role", "secondary"),
                    optional=line.get("optional", False),
                    choice_group=line.get("choice_group"),
                )
            )
        for name in dish.get("cookware", []):
            recipe.cookware.append(RecipeCookware(cookware_id=_one(session, Cookware, name).id))
        session.add(recipe)
        recipes.append(recipe)
    session.flush()
    return recipes


def _one(session: Session, model: type, name_zh: str):
    row = session.scalars(select(model).where(model.name_zh == name_zh)).one_or_none()
    if row is None:
        raise ValueError(f"{name_zh} is not in the {model.__tablename__} dictionary")
    return row


def reset_scenario_state(session: Session, scenario: Scenario) -> None:
    """Make inventory, owned cookware, rules, and the request style the scenario's own."""
    session.execute(delete(Inventory))
    session.execute(delete(CookwareOwned))
    session.execute(delete(DietaryRule))
    for name, amount in scenario.inventory.items():
        ingredient = _one(session, Ingredient, name)
        by_level = isinstance(amount, str)
        session.add(
            Inventory(
                ingredient_id=ingredient.id,
                count=None if by_level else amount,
                level=amount if by_level else None,
            )
        )
    for name in scenario.cookware:
        session.add(CookwareOwned(cookware_id=_one(session, Cookware, name).id))
    diner_id = session.scalar(select(Diner.id).where(Diner.is_self))
    for item in scenario.rules:
        ingredient = item.get("ingredient")
        session.add(
            DietaryRule(
                diner_id=diner_id,
                rule_type=item["type"],
                allergen=item.get("allergen"),
                ingredient_id=_one(session, Ingredient, ingredient).id if ingredient else None,
                text=item.get("text"),
            )
        )
    session.commit()
    set_request_style(session, "plan_then_adjust")


# ---- running ----


def _plan_detail(plan: dict[str, Any] | None) -> str:
    if not plan:
        return "no plan"
    titles = ", ".join(r["title"] for r in plan["recipes"]) or "no recipes shown"
    parts = [f"recipes: {titles}"]
    if plan["unmet"]:
        parts.append("unmet: " + ", ".join(sorted({u["code"] for u in plan["unmet"]})))
    if plan["left_out_unknown_allergens"]:
        parts.append("left out (unknown allergens): " + ", ".join(plan["left_out_unknown_allergens"]))
    return "; ".join(parts)


def run_scenario(
    graph, factory: sessionmaker[Session], recorder: PlanningRecorder, scenario: Scenario
) -> RunOutcome:
    with factory() as session:
        reset_scenario_state(session, scenario)
        watermark = session.scalar(select(func.coalesce(func.max(LlmCall.id), 0)))
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    note = ""
    try:
        graph.invoke(
            {"messages": [HumanMessage(scenario.request)], "locale": scenario.locale}, config
        )
        pending = graph.get_state(config).interrupts
        if pending:
            note = f"stopped at a question: {pending[0].value.get('kind')}"
    except Exception as error:
        # Includes AllModelsFailed and MonthlyCapExceeded: record it and go on.
        note = f"failed: {type(error).__name__}"
    state = graph.get_state(config).values
    if note and state.get("run_id") is not None:
        recorder.fail(state["run_id"], state.get("retries") or 0)
    with factory() as session:
        run = session.scalars(
            select(PlanningRun).where(PlanningRun.thread_id == thread_id)
        ).one_or_none()
        calls, cost, latency = session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LlmCall.cost_estimate), 0),
                func.coalesce(func.sum(LlmCall.latency_ms), 0),
            ).where(LlmCall.id > watermark, LlmCall.task == EVAL_TASK)
        ).one()
    return RunOutcome(
        scenario=scenario.name,
        first_passed=bool(run and run.first_passed),
        final_passed=bool(run and run.final_passed),
        retries=run.retries if run else 0,
        cost=Decimal(cost),
        latency_ms=int(latency),
        model_calls=calls,
        note=note,
        detail=_plan_detail(state.get("current_plan")),
        expected_plan=scenario.expect_plan,
    )


def evaluate(
    factory: sessionmaker[Session],
    client: LLMClient,
    library: list[dict[str, Any]],
    scenarios: list[Scenario],
    checkpointer,
    on_outcome: Callable[[RunOutcome], None] | None = None,
) -> list[RunOutcome]:
    """Seed, load the library, run every scenario, and always empty the app tables after.

    Refuses before touching anything when the database already holds recipes or ingredients.
    """
    with factory() as session:
        ensure_empty(session)
    outcomes: list[RunOutcome] = []
    try:
        with factory() as session:
            seed_all(session)
            load_library(session, library)
            session.commit()
        recorder = PlanningRecorder(factory)
        graph = build_graph(GraphDeps(factory, client, recorder, task=EVAL_TASK), checkpointer)
        for scenario in scenarios:
            outcome = run_scenario(graph, factory, recorder, scenario)
            outcomes.append(outcome)
            if on_outcome:
                on_outcome(outcome)
    finally:
        clear_app_tables(factory)
    return outcomes


# ---- report ----


def _line(outcome: RunOutcome) -> str:
    matched = outcome.final_passed == outcome.expected_plan
    return (
        f"{outcome.scenario}: first={'pass' if outcome.first_passed else 'fail'} "
        f"final={'pass' if outcome.final_passed else 'fail'} retries={outcome.retries} "
        f"model_calls={outcome.model_calls} cost=${outcome.cost:.4f} "
        f"latency={outcome.latency_ms}ms "
        f"expected_plan={'yes' if outcome.expected_plan else 'no'} "
        f"as_expected={'yes' if matched else 'no'}"
    )


def print_outcome(outcome: RunOutcome) -> None:
    print(_line(outcome))
    print(f"  {outcome.detail}")
    if outcome.note:
        print(f"  note: {outcome.note}")


def print_summary(outcomes: list[RunOutcome]) -> None:
    s = summarize(outcomes)
    print()
    print(f"Overall over {len(outcomes)} scenarios")
    print(f"  first-attempt pass rate: {s['first_pass_rate']:.2f}")
    print(f"  final pass rate:         {s['final_pass_rate']:.2f}")
    print(f"  average retries:         {s['avg_retries']:.2f}")
    print(f"  average model calls:     {s['avg_model_calls']:.1f}")
    print(f"  cost (USD):              {s['cost_usd']:.4f}")
    print(f"  average latency:         {s['avg_latency_ms']} ms")
    print(
        "  (latency is summed over each scenario's model calls; a failed run counts as not passed)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        check_test_database(settings)
        library = read_library(args.library)
        scenarios = load_scenarios(args.scenarios)
    except (UnsafeDatabase, InputError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    client = LLMClient(
        load_llm_config(model_overrides=settings.model_overrides()),
        factory,
        settings.llm_monthly_cap_usd,
    )
    print(f"Planning evaluation of {len(scenarios)} scenarios (task {EVAL_TASK})")
    try:
        with open_checkpointer(settings.database_url) as saver:
            outcomes = evaluate(factory, client, library, scenarios, saver, print_outcome)
    except (UnsafeDatabase, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print_summary(outcomes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
