"""Minimal planning evaluation: fictional scenarios through the planning graph.

Runs only against the test database, which must be empty. It seeds the dictionary, loads a
fictional recipe library, runs each scenario on a fresh thread with the real planning models
(task `planning_eval`, counted and capped), prints the results, and empties the app tables.
"""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
from minemydinner.recipes.embedding import try_embed_recipe
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


class UnsafeWorkingTree(Exception):
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
    # For the saved results: the graph thread, the PlanningRun row, and the shown titles.
    thread_id: str = ""
    planning_run_id: int | None = None
    titles: list[str] = field(default_factory=list)
    cache_write_input_tokens: int = 0
    # Embedding calls made during the scenario (the meaning-based search of its queries).
    embedding_calls: int = 0
    # Scenario start to finish (planner, database, and tool time included), in ms.
    wall_ms: int = 0
    # The latency of each planning_eval model call (latency_ms is their sum).
    call_latencies: list[int] = field(default_factory=list)
    # For results.jsonl only (never in summary.json): the PlanningRun row and the llm_calls
    # rows of the scenario.
    record: dict[str, Any] = field(default_factory=dict)


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
    started = time.perf_counter()
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
        all_rows = session.scalars(
            select(LlmCall).where(LlmCall.id > watermark).order_by(LlmCall.id)
        ).all()
        rows = [r for r in all_rows if r.task == EVAL_TASK]
        embedding_calls = sum(r.task == "embeddings" for r in all_rows)
        record = {
            "planning_run": _run_row(run),
            "llm_calls": [_call_row(r) for r in all_rows],
        }
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
        thread_id=thread_id,
        planning_run_id=run.id if run else None,
        titles=[r["title"] for r in (plan or {}).get("recipes", [])],
        cache_write_input_tokens=sum(r.cache_write_input_tokens or 0 for r in rows),
        embedding_calls=embedding_calls,
        wall_ms=int((time.perf_counter() - started) * 1000),
        call_latencies=[r.latency_ms or 0 for r in rows],
        record=record,
    )


def _run_row(run: PlanningRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "thread_id": run.thread_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "prompt_version": run.prompt_version,
        "tools_version": run.tools_version,
        "request": run.request,
        "defaults_used": list(run.defaults_used),
        "events": run.events,
        "retries": run.retries,
        "first_passed": run.first_passed,
        "final_passed": run.final_passed,
        "final_plan": run.final_plan,
        "has_temporary_guests": run.has_temporary_guests,
    }


def _call_row(call: LlmCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "task": call.task,
        "provider": call.provider,
        "model": call.model,
        "input_tokens": call.input_tokens,
        "cached_input_tokens": call.cached_input_tokens,
        "cache_write_input_tokens": call.cache_write_input_tokens,
        "output_tokens": call.output_tokens,
        "cost_estimate": call.cost_estimate,
        "latency_ms": call.latency_ms,
        "success": call.success,
        "fallback_used": call.fallback_used,
        "created_at": call.created_at.isoformat() if call.created_at else None,
    }


def evaluate(
    factory: sessionmaker[Session],
    client: LLMClient,
    library: list[dict[str, Any]],
    scenarios: list[Scenario],
    checkpointer,
    on_outcome: Callable[[RunOutcome], None] | None = None,
    use_shortlist: bool = True,
    with_embeddings: bool = False,
    stats: dict[str, int] | None = None,
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
            recipes = load_library(session, library)
            session.commit()
            if with_embeddings:
                # One embedding call per recipe, so meaning-based search works in the run.
                failed = sum(not try_embed_recipe(session, client, recipe) for recipe in recipes)
                if stats is not None:
                    stats["library_embedding_calls"] = len(recipes)
                    stats["library_embeddings_failed"] = failed
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
    plan = group_metrics([o for o in scenario_runs if o.expected_plan])
    if plan["scenarios"]:
        latency = plan["latency"]
        print(f"Scenarios that expect a plan ({plan['scenarios']})")
        print(f"  as expected:             {plan['outcomes']['as_expected']}")
        print(f"  first-attempt pass rate: {plan['outcomes']['first_pass_rate']:.2f}")
        print(f"  average model calls:     {plan['calls']['avg_model_calls']:.1f}")
        print(f"  cost per scenario (USD): {plan['cost']['avg_per_scenario']['cache_aware_usd']}")
        print(
            "  wall time avg/p50/p95:   "
            f"{latency['wall_ms']['avg']}/{latency['wall_ms']['p50']}/{latency['wall_ms']['p95']} ms"
        )
        print(
            "  per model call avg/p95:  "
            f"{latency['per_call_ms']['avg']}/{latency['per_call_ms']['p95']} ms"
        )


# ---- saved results ----

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_ROOT = Path(__file__).resolve().parents[2] / "eval_data" / "planning" / "runs"
# Run folders are written by the evaluation itself, so they never count as uncommitted work.
RUNS_PREFIX = "backend/eval_data/planning/runs/"


def _git(*args: str) -> str | None:
    """Output of a read-only git command, or None when git is unavailable or fails."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def ensure_clean_tree() -> None:
    """Refuse to run with uncommitted changes (outside the run folders) or without git, so
    every run's recorded commit is exactly the code that ran."""
    status = _git("status", "--porcelain", "--untracked-files=all")
    if status is None:
        raise UnsafeWorkingTree("git is unavailable, so the working tree cannot be checked; refusing to run")
    for line in status.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if line.strip() and not path.startswith(RUNS_PREFIX):
            raise UnsafeWorkingTree(
                "The working tree has uncommitted changes; commit them before a run"
            )


def _file_info(path: Path) -> dict[str, str]:
    try:
        shown = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        shown = path.name
    return {"path": shown, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _task_models(client: LLMClient, task: str) -> dict[str, Any] | None:
    config = client.config.tasks.get(task)
    if config is None:
        return None
    candidates = []
    for name in config.candidates:
        model = client.config.models[name]
        candidates.append(
            {
                "provider": model.provider,
                "model": model.model,
                "input_usd_per_mtok": model.input_usd_per_mtok,
                "cached_input_usd_per_mtok": model.cached_input_usd_per_mtok,
                "cache_write_usd_per_mtok": model.cache_write_usd_per_mtok,
                "output_usd_per_mtok": model.output_usd_per_mtok,
            }
        )
    return {"essential": config.essential, "candidates": candidates}


def run_info(
    scenarios_path: Path,
    library_path: Path,
    client: LLMClient,
    no_shortlist: bool,
    with_embeddings: bool,
    git_commit: str | None,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """The run's times and conditions for summary.json. Holds no URL, key, or environment."""
    # Imported here: only the saved results need these constants and versions.
    from minemydinner.agent.planner import (
        MODEL_CALL_LIMIT,
        PLANNER_INPUT_TOKEN_BUDGET,
        TOOL_CALL_LIMIT,
        TOOLS_VERSION,
    )
    from minemydinner.agent.request import PROMPT_VERSION
    from minemydinner.planning import shortlist

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "conditions": {
            "git_commit": git_commit,
            "scenarios_file": _file_info(scenarios_path),
            "library_file": _file_info(library_path),
            "prompt_version": PROMPT_VERSION,
            "tools_version": TOOLS_VERSION,
            "flags": {"no_shortlist": no_shortlist, "with_embeddings": with_embeddings},
            "constants": {
                "MODEL_CALL_LIMIT": MODEL_CALL_LIMIT,
                "TOOL_CALL_LIMIT": TOOL_CALL_LIMIT,
                "PLANNER_INPUT_TOKEN_BUDGET": PLANNER_INPUT_TOKEN_BUDGET,
                "SHORTLIST_MIN_PER_CATEGORY": shortlist.SHORTLIST_MIN_PER_CATEGORY,
                "SHORTLIST_MARGIN": shortlist.SHORTLIST_MARGIN,
                "SHORTLIST_SAME_MAIN": shortlist.SHORTLIST_SAME_MAIN,
            },
            "models": {
                EVAL_TASK: _task_models(client, EVAL_TASK),
                "embeddings": _task_models(client, "embeddings"),
                "prices_updated": client.config.prices_updated.isoformat(),
            },
        },
    }


def make_run_id(
    started: datetime, scenarios_path: Path, no_shortlist: bool, with_embeddings: bool
) -> str:
    stamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = scenarios_path.stem.removeprefix("scenarios_")
    shortlist_part = "no-shortlist" if no_shortlist else "shortlist"
    vectors_part = "vectors" if with_embeddings else "no-vectors"
    return f"{stamp}-{name}-{shortlist_part}-{vectors_part}"


def percentile(values: list[int], pct: float) -> int:
    """Nearest-rank percentile; 0 for no values."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(math.ceil(pct / 100 * len(ordered)), 1) - 1]


def _entry(outcome: RunOutcome) -> dict[str, Any]:
    item = asdict(outcome)
    item.pop("record")
    item["name"] = item.pop("scenario")
    item["model_time_ms"] = item.pop("latency_ms")
    item.pop("call_latencies")
    if outcome.check_only:
        item["thread_id"] = None
        item["planning_run_id"] = None
    return item


METRICS_BASIS = (
    "model_time_ms: sum of the scenario's model-call latencies; wall_ms: scenario start to "
    "finish (database and tool time included); per_call: individual planning_eval model "
    "calls. Groups: all and expect_plan/expect_stop (by the scenario's expect_plan); "
    "check-only scenarios are in no group. Aggregates compare only within the same scenario "
    "set; compare same-named scenarios across different sets."
)


def _stats(values: list[int], with_max: bool = False) -> dict[str, int]:
    result = {
        "avg": round(sum(values) / len(values)) if values else 0,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
    }
    if with_max:
        result["max"] = max(values, default=0)
    return result


def _average(total: Decimal, count: int) -> Decimal:
    return (total / count).quantize(Decimal("0.000001")) if count else Decimal("0")


def group_metrics(
    runs: list[RunOutcome], library_embedding_calls: int | None = None
) -> dict[str, Any]:
    """The metrics of one group of scenario runs (never check-only ones). Pure.
    `library_embedding_calls` adds the library's embedding calls (the "all" group only)."""
    n = len(runs)
    s = summarize(runs)
    embeddings: dict[str, int] = {"scenarios": sum(o.embedding_calls for o in runs)}
    if library_embedding_calls is not None:
        embeddings = {
            "library": library_embedding_calls,
            "scenarios": embeddings["scenarios"],
            "total": library_embedding_calls + embeddings["scenarios"],
        }
    inputs = sum(o.input_tokens for o in runs)
    cached = sum(o.cached_input_tokens for o in runs)
    written = sum(o.cache_write_input_tokens for o in runs)
    per_call = [latency for o in runs for latency in o.call_latencies]
    return {
        "scenarios": n,
        "outcomes": {
            "first_pass_rate": s["first_pass_rate"],
            "final_pass_rate": s["final_pass_rate"],
            "as_expected": s["as_expected"],
            "avg_retries": s["avg_retries"],
        },
        "calls": {
            "model_calls": sum(o.model_calls for o in runs),
            "avg_model_calls": s["avg_model_calls"],
            "max_model_calls": max((o.model_calls for o in runs), default=0),
            "embedding_calls": embeddings,
        },
        "tokens": {
            "input": inputs,
            "cached": cached,
            "written": written,
            "avg_per_scenario": {
                "input": round(inputs / n) if n else 0,
                "cached": round(cached / n) if n else 0,
                "written": round(written / n) if n else 0,
            },
            "cache_hit_rate": s["cache_hit_rate"],
            "max_call_input": s["max_call_input_tokens"],
        },
        "cost": {
            "cache_aware_usd": s["cost_usd"],
            "uncached_usd": s["uncached_cost_usd"],
            "avg_per_scenario": {
                "cache_aware_usd": _average(s["cost_usd"], n),
                "uncached_usd": _average(s["uncached_cost_usd"], n),
            },
        },
        "latency": {
            "wall_ms": _stats([o.wall_ms for o in runs], with_max=True),
            "model_time_ms": _stats([o.latency_ms for o in runs]),
            "per_call_ms": _stats(per_call),
        },
    }


def build_summary(
    run_id: str, info: dict[str, Any], outcomes: list[RunOutcome], library_embedding_calls: int
) -> dict[str, Any]:
    """summary.json: conditions, timing, metrics, and per-scenario results, with no model
    text (the `detail` text and the record stay in results.jsonl)."""
    runs = [o for o in outcomes if not o.check_only]
    s = summarize(outcomes)
    started = datetime.fromisoformat(info["started_at"])
    finished = datetime.fromisoformat(info["finished_at"])
    scenarios = []
    for outcome in outcomes:
        item = _entry(outcome)
        item.pop("detail")
        scenarios.append(item)
    return {
        "run_id": run_id,
        "started_at": info["started_at"],
        "finished_at": info["finished_at"],
        "conditions": info["conditions"],
        "timing": {"total_seconds": round((finished - started).total_seconds(), 1)},
        "metrics": {
            "basis": METRICS_BASIS,
            "checks": s["checks"],
            "groups": {
                "all": group_metrics(runs, library_embedding_calls),
                "expect_plan": group_metrics([o for o in runs if o.expected_plan]),
                "expect_stop": group_metrics([o for o in runs if not o.expected_plan]),
            },
        },
        "scenarios": scenarios,
    }


def _tools_document() -> dict[str, Any]:
    from minemydinner.agent.planner import PlanProposal
    from minemydinner.planning.tools import PLANNER_TOOLS

    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "args_schema": tool.tool_call_schema.model_json_schema(),
            }
            for tool in PLANNER_TOOLS
        ],
        "final_answer": {"name": "PlanProposal", "schema": PlanProposal.model_json_schema()},
    }


def _dump(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, default=str)


def write_run(
    root: Path, run_id: str, summary: dict[str, Any], outcomes: list[RunOutcome]
) -> Path:
    """Write the run folder: summary.json, results.jsonl (git-ignored), tools.json, and the
    prompt files of the version used. Returns the folder."""
    from minemydinner.agent.request import PROMPT_DIR

    folder = root / run_id
    (folder / "prompts").mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(_dump(summary, indent=2), encoding="utf-8")
    (folder / "tools.json").write_text(_dump(_tools_document(), indent=2), encoding="utf-8")
    lines = [_dump({**_entry(o), **o.record}) for o in outcomes]
    (folder / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for prompt in sorted(PROMPT_DIR.glob("*.md")):
        shutil.copy2(prompt, folder / "prompts" / prompt.name)
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument(
        "--no-shortlist",
        action="store_true",
        help="run the planner without the shortlist (tools only), for comparison",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RUNS_ROOT,
        help="the folder that holds the run folders (default: backend/eval_data/planning/runs)",
    )
    parser.add_argument(
        "--with-embeddings",
        action="store_true",
        help="embed the library's recipes first, so meaning-based search works",
    )
    args = parser.parse_args(argv)
    try:
        ensure_clean_tree()
    except UnsafeWorkingTree as error:
        print(error, file=sys.stderr)
        return 2
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
    started = datetime.now(timezone.utc)
    saved: Path | None = None
    failed = True  # until the run completes
    collected: list[RunOutcome] = []
    stats: dict[str, int] = {}

    def on_outcome(outcome: RunOutcome) -> None:
        collected.append(outcome)
        print_outcome(outcome)

    try:
        with open_checkpointer(settings.database_url) as saver:
            outcomes = evaluate(
                factory,
                client,
                library,
                scenarios,
                saver,
                on_outcome,
                use_shortlist=not args.no_shortlist,
                with_embeddings=args.with_embeddings,
                stats=stats,
            )
        failed = False
    except (UnsafeDatabase, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    finally:
        engine.dispose()
        # Best effort, also after a partial run that raised: the outcomes so far are kept.
        if collected:
            finished = datetime.now(timezone.utc)
            try:
                commit = (_git("rev-parse", "HEAD") or "").strip() or None
                info = run_info(
                    args.scenarios,
                    args.library,
                    client,
                    args.no_shortlist,
                    args.with_embeddings,
                    commit,
                    started.isoformat(),
                    finished.isoformat(),
                )
                run_id = make_run_id(
                    started, args.scenarios, args.no_shortlist, args.with_embeddings
                )
                summary = build_summary(
                    run_id, info, collected, stats.get("library_embedding_calls", 0)
                )
                saved = write_run(args.results_dir, run_id, summary, collected)
            except Exception as error:
                # Never let a failed save replace the run's own error.
                print(f"Could not save the results: {type(error).__name__}", file=sys.stderr)
            if saved and failed:
                print(f"Partial results saved to {saved}", file=sys.stderr)
    print_summary(outcomes)
    if saved:
        print(f"Run folder: {saved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
