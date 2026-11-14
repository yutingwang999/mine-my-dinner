"""Minimal planning evaluation: fictional scenarios through the planning graph.

Runs only against the test database, which must be empty. It seeds the dictionary, loads a
fictional recipe library, runs each scenario on a fresh thread with the real planning models
(task `planning_eval`, counted and capped), prints the results, and empties the app tables.
"""

import argparse
import sys
import uuid
from collections import Counter
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.agent.checkpoint import open_checkpointer
from minemydinner.agent.graph import GraphDeps, build_graph
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import ParsedRequest, set_request_style
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
from minemydinner.planning.composition import count_categories
from minemydinner.planning.context import MealConstraints, load_planning_context
from minemydinner.planning.shortlist import (
    SHORTLIST_SAME_MAIN,
    build_shortlist_result,
    main_groups,
)
from minemydinner.planning.validate import PlanSpec
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
    # Token use over the scenario's planning_eval calls; uncached_cost prices every input
    # token (cached and written too) at the full input rate, to compare with plan 1.
    input_tokens: int = 0
    cached_input_tokens: int = 0
    max_call_input_tokens: int = 0
    uncached_cost: Decimal = Decimal("0")
    reasons: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    as_expected: bool = True
    # What did not match the scenario's expectations; empty when as_expected.
    failures: list[str] = field(default_factory=list)
    # A check with no model run (model_calls is 0 by design): kept out of the summary rates.
    check_only: bool = False


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
    # Optional expectations; each one given is checked (check_expectations).
    expect_reasons: list[str] = field(default_factory=list)  # all must be among the reasons
    expect_next_actions: list[str] = field(default_factory=list)  # all must be offered
    expect_any_code: list[str] = field(default_factory=list)  # one of these reasons or unmet
    expect_counts: dict[str, int] = field(default_factory=dict)  # category counts shown
    expect_titles_any: list[str] = field(default_factory=list)  # at least one shown title
    expect_titles_all: list[str] = field(default_factory=list)  # every shown title is listed
    expect_warnings: list[str] = field(default_factory=list)  # warning codes in the plan
    expect_assumed: list[str] = field(default_factory=list)  # fields listed as assumed
    expect_not_assumed: list[str] = field(default_factory=list)  # none of these is assumed
    expect_titles_exact: list[str] = field(default_factory=list)  # shown titles equal this set
    expect_titles_include: list[str] = field(default_factory=list)  # each must be shown
    expect_titles_none: list[str] = field(default_factory=list)  # none may be shown
    expect_first_pass: bool = False  # the first attempt must have passed
    # Each entry must match a reason on the keys it gives (reason, category, needed,
    # available, ingredient, absent_from_stock).
    expect_reason_detail: list[dict[str, Any]] = field(default_factory=list)
    expect_s1: list[dict[str, Any]] = field(default_factory=list)  # {name, needed, have}
    expect_left_out_include: list[str] = field(default_factory=list)
    expect_missing_nonempty: bool = False
    expect_locale_reply: str | None = None  # "zh" or "en", judged without recipe titles
    max_model_calls: int | None = None
    # Check-only: build the shortlist for this many dishes (shopping mode) and assert that
    # no main ingredient dominates it. The model is not used and the planner not run.
    shortlist_spread: int | None = None


def summarize(all_runs: list[RunOutcome]) -> dict[str, Any]:
    """Rates and averages over the scenario runs; check-only runs are counted apart in
    `checks` ("passed/total") and never in the rates, latency, or cost. Pure."""
    runs = [r for r in all_runs if not r.check_only]
    checks = [r for r in all_runs if r.check_only]
    checks_text = f"{sum(r.as_expected for r in checks)}/{len(checks)}"
    count = len(runs)
    if count == 0:
        return {
            "first_pass_rate": 0.0,
            "final_pass_rate": 0.0,
            "avg_retries": 0.0,
            "cost_usd": Decimal("0"),
            "avg_latency_ms": 0,
            "avg_model_calls": 0.0,
            "as_expected": "0/0",
            "checks": checks_text,
            "cache_hit_rate": 0.0,
            "max_call_input_tokens": 0,
            "uncached_cost_usd": Decimal("0"),
        }
    input_total = sum(r.input_tokens for r in runs)
    return {
        "first_pass_rate": sum(r.first_passed for r in runs) / count,
        "final_pass_rate": sum(r.final_passed for r in runs) / count,
        "avg_retries": sum(r.retries for r in runs) / count,
        "cost_usd": sum((r.cost for r in runs), Decimal("0")),
        "avg_latency_ms": round(sum(r.latency_ms for r in runs) / count),
        "avg_model_calls": sum(r.model_calls for r in runs) / count,
        "as_expected": f"{sum(r.as_expected for r in runs)}/{count}",
        "checks": checks_text,
        "cache_hit_rate": (
            sum(r.cached_input_tokens for r in runs) / input_total if input_total else 0.0
        ),
        "max_call_input_tokens": max(r.max_call_input_tokens for r in runs),
        "uncached_cost_usd": sum((r.uncached_cost for r in runs), Decimal("0")),
    }


def uncached_cost(
    rows: list[tuple[str, str, int | None, int | None]],
    prices: dict[tuple[str, str], tuple[Decimal, Decimal]],
) -> Decimal:
    """Cost of (provider, model, input tokens, output tokens) rows with every input token at
    the full input rate. `prices` maps (provider, model) to (input, output) USD per million
    tokens; a row whose model is not priced counts as zero."""
    total = Decimal("0")
    for provider, model, input_tokens, output_tokens in rows:
        price = prices.get((provider, model))
        if price is None:
            continue
        total += (
            Decimal(input_tokens or 0) * price[0] + Decimal(output_tokens or 0) * price[1]
        ) / Decimal(1_000_000)
    return total


# ---- expectations ----


_CJK = re.compile("[一-鿿]")


def check_expectations(
    scenario: Scenario,
    plan: dict[str, Any] | None,
    passed: bool,
    model_calls: int,
    first_passed: bool = True,
    reply: str = "",
    library_titles: Sequence[str] = (),
) -> list[str]:
    """What did not match the scenario. `expect_plan` is always checked; the other
    expectations only when the scenario states them. Pure."""
    failures = []
    if passed != scenario.expect_plan:
        failures.append(f"plan passed={passed}, expected {scenario.expect_plan}")
    plan = plan or {}
    reasons = [r["reason"] for r in plan.get("reasons", [])]
    actions = plan.get("next_actions", [])
    unmet = [u["code"] for u in plan.get("unmet", [])]
    recipes = plan.get("recipes", [])
    missing = [r for r in scenario.expect_reasons if r not in reasons]
    if missing:
        failures.append(f"missing reason {missing}; got {reasons}")
    missing = [a for a in scenario.expect_next_actions if a not in actions]
    if missing:
        failures.append(f"missing next action {missing}; got {actions}")
    if scenario.expect_any_code and not set(scenario.expect_any_code) & {*reasons, *unmet}:
        failures.append(
            f"none of {scenario.expect_any_code} among reasons {reasons} or unmet {unmet}"
        )
    if scenario.expect_counts:
        counts = count_categories(r["dish_type"] for r in recipes)
        wrong = {
            k: (counts.get(k, 0), v)
            for k, v in scenario.expect_counts.items()
            if counts.get(k, 0) != v
        }
        if wrong:
            failures.append(f"counts (got, expected): {wrong}")
    titles = [r["title"] for r in recipes]
    if scenario.expect_titles_any and not set(titles) & set(scenario.expect_titles_any):
        failures.append(f"none of {scenario.expect_titles_any} shown; got {titles}")
    if scenario.expect_titles_all:
        extra = [t for t in titles if t not in scenario.expect_titles_all]
        if extra or not titles:
            failures.append(f"titles outside {scenario.expect_titles_all}: {extra or 'no recipes'}")
    warning_codes = [w["code"] for w in plan.get("warnings", [])]
    missing = [w for w in scenario.expect_warnings if w not in warning_codes]
    if missing:
        failures.append(f"missing warning {missing}; got {warning_codes}")
    assumed = plan.get("defaults_used", [])
    missing = [a for a in scenario.expect_assumed if a not in assumed]
    if missing:
        failures.append(f"not stated as assumed {missing}; got {assumed}")
    shown = set(titles)
    if scenario.expect_titles_exact and shown != set(scenario.expect_titles_exact):
        failures.append(f"titles {sorted(shown)}, expected exactly {scenario.expect_titles_exact}")
    absent = [t for t in scenario.expect_titles_include if t not in shown]
    if absent:
        failures.append(f"titles not shown: {absent}")
    present = [t for t in scenario.expect_titles_none if t in shown]
    if present:
        failures.append(f"titles that must not be shown: {present}")
    wrongly = [a for a in scenario.expect_not_assumed if a in assumed]
    if wrongly:
        failures.append(f"assumed but stated: {wrongly}")
    if scenario.expect_first_pass and not first_passed:
        failures.append("the first attempt did not pass")
    entries = plan.get("reasons", [])
    for wanted in scenario.expect_reason_detail:
        if not any(all(e.get(k) == v for k, v in wanted.items()) for e in entries):
            failures.append(f"no reason matching {wanted}; got {entries}")
    s1 = [w.get("detail", {}) for w in plan.get("warnings", []) if w.get("code") == "S1"]
    for wanted in scenario.expect_s1:
        if not any(all(d.get(k) == v for k, v in wanted.items()) for d in s1):
            failures.append(f"no S1 warning matching {wanted}; got {s1}")
    left_out = plan.get("left_out_unknown_allergens", [])
    absent = [t for t in scenario.expect_left_out_include if t not in left_out]
    if absent:
        failures.append(f"not left out for unknown allergens: {absent}; got {left_out}")
    if scenario.expect_missing_nonempty and not any(plan.get("missing", {}).values()):
        failures.append("the plan's missing list is empty")
    if scenario.expect_locale_reply:
        text = reply
        for title in sorted(library_titles, key=len, reverse=True):
            text = text.replace(title, "")
        has_cjk = bool(_CJK.search(text))
        if scenario.expect_locale_reply == "en" and has_cjk:
            failures.append("reply is not English (Chinese characters left after removing titles)")
        if scenario.expect_locale_reply == "zh" and not has_cjk:
            failures.append("reply is not Chinese (no Chinese characters besides titles)")
    if scenario.max_model_calls is not None and model_calls > scenario.max_model_calls:
        failures.append(f"{model_calls} model calls, at most {scenario.max_model_calls}")
    return failures


def check_shortlist_spread(
    ids: list[int], groups: dict[int, frozenset[int] | None], max_same: int
) -> list[str]:
    """Failures when more than `max_same` shortlisted recipes share one main-ingredient
    group. Recipes with no group are never counted. Pure."""
    counts = Counter(groups.get(i) for i in ids if groups.get(i) is not None)
    return [
        f"{n} shortlisted recipes share main ingredients {sorted(group)}"
        for group, n in counts.items()
        if n > max_same
    ]


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
                    # An exact count of pieces when the line gives `amount`, else vague.
                    amount=line.get("amount"),
                    unit="piece" if line.get("amount") is not None else None,
                    amount_type="count" if line.get("amount") is not None else "vague",
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


def _spread_outcome(
    factory: sessionmaker[Session], client: LLMClient, scenario: Scenario
) -> RunOutcome:
    """Check-only scenario: for a library where one main ingredient dominates, the shortlist
    keeps at most SHORTLIST_SAME_MAIN dishes per main-ingredient group. No planner run."""
    with factory() as session:
        reset_scenario_state(session, scenario)
        diner_id = session.scalar(select(Diner.id).where(Diner.is_self))
        ctx = load_planning_context(session, MealConstraints(diner_ids=(diner_id,)))
        spec = PlanSpec(mode="shopping", dishes=scenario.shortlist_spread, soups=0)
        found = build_shortlist_result(session, client, ctx, spec, ParsedRequest(), {})
        entries = [e for items in found.entries.values() for e in items]
        ids = [e.id for e in entries]
        groups = main_groups(session, ctx, ids, [])
    failures = check_shortlist_spread(ids, groups, SHORTLIST_SAME_MAIN)
    return RunOutcome(
        scenario=scenario.name,
        first_passed=not failures,
        final_passed=not failures,
        retries=0,
        cost=Decimal("0"),
        latency_ms=0,
        model_calls=0,
        note="check-only: shortlist spread",
        detail="shortlist: " + ", ".join(e.title for e in entries),
        as_expected=not failures,
        failures=failures,
        check_only=True,
    )


def run_scenario(
    graph,
    factory: sessionmaker[Session],
    recorder: PlanningRecorder,
    scenario: Scenario,
    prices: dict[tuple[str, str], tuple[Decimal, Decimal]] | None = None,
    client: LLMClient | None = None,
    library_titles: Sequence[str] = (),
) -> RunOutcome:
    if scenario.shortlist_spread is not None:
        if client is None:
            raise ValueError(f"Scenario {scenario.name} is check-only and needs a client")
        return _spread_outcome(factory, client, scenario)
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
        rows = session.execute(
            select(
                LlmCall.provider,
                LlmCall.model,
                LlmCall.input_tokens,
                LlmCall.output_tokens,
                LlmCall.cached_input_tokens,
                LlmCall.cost_estimate,
                LlmCall.latency_ms,
            ).where(LlmCall.id > watermark, LlmCall.task == EVAL_TASK)
        ).all()
    plan = state.get("current_plan")
    passed = bool(run and run.final_passed)
    first_passed = bool(run and run.first_passed)
    reply = next(
        (
            str(m.content)
            for m in reversed(state.get("messages") or [])
            if isinstance(m, AIMessage)
        ),
        "",
    )
    failures = check_expectations(
        scenario, plan, passed, len(rows), first_passed, reply, library_titles
    )
    return RunOutcome(
        scenario=scenario.name,
        first_passed=bool(run and run.first_passed),
        final_passed=passed,
        retries=run.retries if run else 0,
        cost=sum((Decimal(r.cost_estimate or 0) for r in rows), Decimal("0")),
        latency_ms=sum(r.latency_ms or 0 for r in rows),
        model_calls=len(rows),
        note=note,
        detail=_plan_detail(plan),
        expected_plan=scenario.expect_plan,
        input_tokens=sum(r.input_tokens or 0 for r in rows),
        cached_input_tokens=sum(r.cached_input_tokens or 0 for r in rows),
        max_call_input_tokens=max((r.input_tokens or 0 for r in rows), default=0),
        uncached_cost=uncached_cost(
            [(r.provider, r.model, r.input_tokens, r.output_tokens) for r in rows], prices or {}
        ),
        reasons=[r["reason"] for r in (plan or {}).get("reasons", [])],
        next_actions=list((plan or {}).get("next_actions", [])),
        as_expected=not failures,
        failures=failures,
    )


def evaluate(
    factory: sessionmaker[Session],
    client: LLMClient,
    library: list[dict[str, Any]],
    scenarios: list[Scenario],
    checkpointer,
    on_outcome: Callable[[RunOutcome], None] | None = None,
    use_shortlist: bool = True,
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
        graph = build_graph(
            GraphDeps(factory, client, recorder, task=EVAL_TASK, use_shortlist=use_shortlist),
            checkpointer,
        )
        prices = {
            (m.provider, m.model): (m.input_usd_per_mtok, m.output_usd_per_mtok)
            for m in client.config.models.values()
        }
        for scenario in scenarios:
            outcome = run_scenario(
                graph,
                factory,
                recorder,
                scenario,
                prices,
                client,
                [dish["title"] for dish in library],
            )
            outcomes.append(outcome)
            if on_outcome:
                on_outcome(outcome)
    finally:
        clear_app_tables(factory)
    return outcomes


# ---- report ----


def _line(outcome: RunOutcome) -> str:
    return (
        f"{outcome.scenario}: first={'pass' if outcome.first_passed else 'fail'} "
        f"final={'pass' if outcome.final_passed else 'fail'} retries={outcome.retries} "
        f"model_calls={outcome.model_calls} cost=${outcome.cost:.4f} "
        f"uncached_cost=${outcome.uncached_cost:.4f} latency={outcome.latency_ms}ms "
        f"cache_hit={_rate(outcome.cached_input_tokens, outcome.input_tokens):.2f} "
        f"max_call_input={outcome.max_call_input_tokens} "
        f"expected_plan={'yes' if outcome.expected_plan else 'no'} "
        f"as_expected={'yes' if outcome.as_expected else 'no'}"
    )


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def print_outcome(outcome: RunOutcome) -> None:
    print(_line(outcome))
    print(f"  {outcome.detail}")
    if outcome.reasons or outcome.next_actions:
        print(f"  reasons: {outcome.reasons}; next actions: {outcome.next_actions}")
    for failure in outcome.failures:
        print(f"  MISMATCH: {failure}")
    if outcome.note:
        print(f"  note: {outcome.note}")


def print_summary(outcomes: list[RunOutcome]) -> None:
    s = summarize(outcomes)
    print()
    scenario_runs = [o for o in outcomes if not o.check_only]
    print(f"Overall over {len(scenario_runs)} scenarios")
    print(f"  first-attempt pass rate: {s['first_pass_rate']:.2f}")
    print(f"  final pass rate:         {s['final_pass_rate']:.2f}")
    print(f"  average retries:         {s['avg_retries']:.2f}")
    print(f"  average model calls:     {s['avg_model_calls']:.1f}")
    print(f"  as expected:             {s['as_expected']}")
    print(f"  checks: {s['checks']} passed")
    print(f"  cost (USD, cache-aware): {s['cost_usd']:.4f}")
    print(f"  cost (USD, uncached):    {s['uncached_cost_usd']:.4f}  (plan 1 baseline: 0.064)")
    print(f"  cache hit rate:          {s['cache_hit_rate']:.2f}")
    print(f"  largest call input:      {s['max_call_input_tokens']} tokens")
    print(f"  average latency:         {s['avg_latency_ms']} ms")
    print(
        "  (latency is summed over each scenario's model calls; a failed run counts as not passed)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument(
        "--no-shortlist",
        action="store_true",
        help="run the planner without the shortlist (tools only), for comparison",
    )
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
            outcomes = evaluate(
                factory,
                client,
                library,
                scenarios,
                saver,
                print_outcome,
                use_shortlist=not args.no_shortlist,
            )
    except (UnsafeDatabase, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print_summary(outcomes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
