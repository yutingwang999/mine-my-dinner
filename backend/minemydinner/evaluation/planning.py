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
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from sqlalchemy import create_engine, delete, func, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.agent.checkpoint import open_checkpointer
from minemydinner.agent.edits import EditError, newest_reply_id
from minemydinner.agent.graph import CLOSEST_NOTE_EN, CLOSEST_NOTE_ZH, GraphDeps, build_graph
from minemydinner.agent.diners import DinerAnswer, GuestAnswer, RuleChoice
from minemydinner.agent.labels import (
    Friend,
    find_friends,
    food_terms,
    replace_nicknames,
    restore_labels,
)
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import REQUEST_STYLES, ParsedRequest, set_request_style
from minemydinner.dictionary.service import resolve_allergens
from minemydinner.extraction.normalize import all_terms, find_term_owner
from minemydinner.llm.client import LLMClient
from minemydinner.llm.config import (
    ConfigError,
    load_llm_config,
    pin_task_model,
    tool_calling_models,
)
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
from minemydinner.evaluation.revise_eval import (
    CHOICE_STOP,
    NOT_IN_ANY_OF,
    FollowUpSetupError,
    TurnFacts,
    apply_edits,
    asked_back,
    bring_back,
    check_revise_keys,
    follow_up_failures,
    hide_dishes,
    locked_of,
    locked_removed,
    plan_dishes,
    replace_first_plan,
    route_of,
    title_problems,
)
from minemydinner.recipes.embedding import try_embed_recipe
from minemydinner.planning.composition import count_categories
from minemydinner.planning.context import MealConstraints, RuleRef, load_planning_context
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
    # The shown plan's own model note (without the fixed closest-attempt text), its automatic
    # flags (note_flags), and whether there was a note to check. The text stays out of
    # summary.json.
    reply_note: str = ""
    note_flags: list[str] = field(default_factory=list)
    note_checked: bool = False
    # Whether a diner card appeared, how many did (over every turn), and the first card's
    # value. The card holds names, so it stays out of summary.json.
    card_shown: bool = False
    cards_shown: int = 0
    card: dict[str, Any] | None = None
    # ---- plan 3b-1: the judged turn (the follow-up, else the first message) ----
    # "plan" (planned from scratch), "revision" (the revise step ran), or "none" (the
    # follow-up made no planning run: an unanswered choice card, an "other" reply).
    # first_passed, final_passed and retries are the judged turn's run's.
    turn: str = "plan"
    # The route model's answer for the judged turn; None when no route call was made.
    route_intent: str | None = None
    # Whether the route matched expect_route; None when the scenario states none.
    route_as_expected: bool | None = None
    # Locked dishes a revision removed without the scenario expecting it (target 0).
    locked_removed: int = 0
    # Dishes of the plan before the follow-up that are gone after it.
    changed: int = 0
    # The later plan the expected result needs: reported, kept out of every rate.
    deferred_to: str | None = None
    # planning_eval model calls of the judged turn only (route, parse, planner or revise
    # attempts); model_calls counts the whole scenario.
    turn_model_calls: int = 0
    # Whether the judged turn is a follow-up (route accuracy is reported apart for follow-ups
    # and first messages).
    follow_up: bool = False


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
    # Category counts shown within [low, high], both ends included ({meat_dishes: [2, 8]}).
    expect_count_range: dict[str, list[int]] = field(default_factory=dict)
    # Values the plan's spec must hold ({all_use: [], prefer_use: [鸡蛋]}; null for None).
    expect_spec: dict[str, Any] = field(default_factory=dict)
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
    # A second user message on the same thread after the first plan, such as the fixed
    # message a next-action button sends. The expectations then judge the second plan, and
    # model calls count both turns.
    follow_up: str = ""
    # Reasons the first message's plan must show (only with follow_up).
    expect_first_reasons: list[str] = field(default_factory=list)
    # Check-only: build the shortlist for this many dishes (shopping mode) and assert that
    # no main ingredient dominates it. The model is not used and the planner not run.
    shortlist_spread: int | None = None
    # ---- plan 2b: diners (spec 5.9) ----
    # Saved friends: [{name, rules: [{type, allergen?, ingredient?, text?}]}].
    friends: list[dict[str, Any]] = field(default_factory=list)
    request_style: str = "plan_then_adjust"  # or ask_first
    # Replies to ask-first (and contradiction) questions, in order.
    answers: list[str] = field(default_factory=list)
    # How the runner answers the diner card: None or "accept" ticks the card's preselection
    # and every proposed rule for this meal only, with unmatched people "none"; a mapping
    # overrides parts of that (selected, guests, others, others_answer).
    diners_answer: str | dict[str, Any] | None = None
    # None: the card is not checked. False: no card may appear. True or a mapping: the first
    # card must appear, with the parts given (preselected, others, nudge, diet_words,
    # guests, self_proposed).
    expect_card: bool | dict[str, Any] | None = None
    # [{who: name | "me" | "guest", allergen? | ingredient? | text?}] against the planning
    # context's rules of the judged turn.
    expect_rules_applied: list[dict[str, Any]] = field(default_factory=list)
    expect_rules_not_applied: list[dict[str, Any]] = field(default_factory=list)
    # Database actions before the follow-up: [{confirm_allergens: {name, allergens: []}}].
    between_turns: list[dict[str, Any]] = field(default_factory=list)
    # ---- plan 3b-1: revising the plan (spec 5.6; the keys are defined in the header of
    # scenarios_revise_heldout.yaml). Titles are library titles. ----
    # After the first message's turn, the newest reply's dishes become these, as a model
    # version (not edited, no locks), so the follow-up starts from a known plan.
    first_plan: list[str] = field(default_factory=list)
    # 3a edits applied in order after between_turns: [{add | remove | keep: title}]; an add
    # or keep that needs confirmation is confirmed.
    edits_between_turns: list[dict[str, str]] = field(default_factory=list)
    # The answer to the choice card (revise | plan); with none the runner stops at the card.
    choice_answer: str | None = None
    # The route result of the follow-up (plan | revise | unsure).
    expect_route: str | None = None
    # Whether the choice card appeared on the follow-up.
    expect_choice_card: bool | None = None
    # The follow-up ends with a question to the user and no new plan version.
    expect_question: bool | None = None
    # The dishes after the follow-up equal those before it.
    expect_unchanged: bool | None = None
    # Each is gone after the follow-up.
    expect_removed: list[str] = field(default_factory=list)
    # At most this many dishes of the plan before the follow-up are gone.
    expect_max_changed: int | None = None
    # At least one of them is gone after the follow-up.
    expect_removed_any: list[str] = field(default_factory=list)
    # Met when the follow-up meets every key of at least one entry ({expect_* keys}); keys
    # outside the list are still all required.
    expect_any_of: list[dict[str, Any]] = field(default_factory=list)
    # Library dishes that do not exist for this scenario (hidden, then brought back).
    library_exclude: list[str] = field(default_factory=list)
    # The expected result needs a later plan: run and reported, kept out of the rates.
    deferred_to: str | None = None


def _ratio(part: float, whole: int) -> float:
    return part / whole if whole else 0.0


def summarize(all_runs: list[RunOutcome]) -> dict[str, Any]:
    """Rates and averages over the scenario runs. Check-only runs are counted apart in
    `checks` ("passed/total") and never in the rates, latency, or cost. Deferred scenarios
    (`deferred_to`) are counted apart in `deferred` ("as expected/total") and kept out of every
    rate, but their cost, calls, tokens and latency count. The planning rates (first and final
    pass, retries) are over new plans only; revisions have their own (spec 5.6). Pure."""
    runs = [r for r in all_runs if not r.check_only]
    checks = [r for r in all_runs if r.check_only]
    checks_text = f"{sum(r.as_expected for r in checks)}/{len(checks)}"
    rated = [r for r in runs if not r.deferred_to]
    deferred = [r for r in runs if r.deferred_to]
    plans = [r for r in rated if r.turn == "plan"]
    revisions = [r for r in rated if r.turn == "revision"]
    routed = [r for r in rated if r.route_as_expected is not None and r.follow_up]
    routed_first = [r for r in rated if r.route_as_expected is not None and not r.follow_up]
    count = len(runs)
    input_total = sum(r.input_tokens for r in runs)
    return {
        "first_pass_rate": _ratio(sum(r.first_passed for r in plans), len(plans)),
        "final_pass_rate": _ratio(sum(r.final_passed for r in plans), len(plans)),
        "avg_retries": _ratio(sum(r.retries for r in plans), len(plans)),
        "cost_usd": sum((r.cost for r in runs), Decimal("0")),
        "avg_latency_ms": round(_ratio(sum(r.latency_ms for r in runs), count)),
        "avg_model_calls": _ratio(sum(r.model_calls for r in runs), count),
        "as_expected": f"{sum(r.as_expected for r in rated)}/{len(rated)}",
        "checks": checks_text,
        "cache_hit_rate": _ratio(sum(r.cached_input_tokens for r in runs), input_total),
        "max_call_input_tokens": max((r.max_call_input_tokens for r in runs), default=0),
        "uncached_cost_usd": sum((r.uncached_cost for r in runs), Decimal("0")),
        "deferred": f"{sum(r.as_expected for r in deferred)}/{len(deferred)}",
        "revisions": len(revisions),
        "revision_pass_rate": _ratio(sum(r.final_passed for r in revisions), len(revisions)),
        "locked_removed": sum(r.locked_removed for r in revisions),
        "avg_changed": _ratio(sum(r.changed for r in revisions), len(revisions)),
        "avg_turn_model_calls": _ratio(
            sum(r.turn_model_calls for r in revisions), len(revisions)
        ),
        "max_turn_model_calls": max((r.turn_model_calls for r in revisions), default=0),
        # Over follow-ups (the brief's route accuracy) and over first messages, apart.
        "route_as_expected": f"{sum(bool(r.route_as_expected) for r in routed)}/{len(routed)}",
        "route_first_as_expected": (
            f"{sum(bool(r.route_as_expected) for r in routed_first)}/{len(routed_first)}"
        ),
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
    first_plan: dict[str, Any] | None = None,
    check_plan_flag: bool = True,
) -> list[str]:
    """What did not match the scenario. `expect_plan` is always checked (unless
    `check_plan_flag` is False: a follow-up that made no planning run); the other expectations
    only when the scenario states them. `first_plan` is the plan of the first message when
    the scenario has a follow-up. Pure."""
    failures = []
    if check_plan_flag and passed != scenario.expect_plan:
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
    if scenario.expect_count_range:
        counts = count_categories(r["dish_type"] for r in recipes)
        outside = {
            k: (counts.get(k, 0), [low, high])
            for k, (low, high) in scenario.expect_count_range.items()
            if not low <= counts.get(k, 0) <= high
        }
        if outside:
            failures.append(f"counts outside range (got, range): {outside}")
    spec = plan.get("spec") or {}
    wrong_spec = {
        k: (spec.get(k), v) for k, v in scenario.expect_spec.items() if spec.get(k) != v
    }
    if wrong_spec:
        failures.append(f"spec (got, expected): {wrong_spec}")
    if scenario.expect_first_reasons:
        first = [r["reason"] for r in (first_plan or {}).get("reasons", [])]
        missing = [r for r in scenario.expect_first_reasons if r not in first]
        if missing:
            failures.append(f"first message: missing reason {missing}; got {first}")
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
        problem = locale_problem(reply, scenario.expect_locale_reply, library_titles)
        if problem:
            failures.append(problem)
    if scenario.max_model_calls is not None and model_calls > scenario.max_model_calls:
        failures.append(f"{model_calls} model calls, at most {scenario.max_model_calls}")
    return failures


# The expect_* keys an expect_any_of entry may hold.
ANY_OF_KEYS = frozenset(
    f.name for f in fields(Scenario) if f.name.startswith("expect_") and f.name not in NOT_IN_ANY_OF
)


def _any_of_entry(scenario: Scenario, entry: dict[str, Any]) -> Scenario:
    """A scenario that states only the entry's keys."""
    return Scenario(
        name=scenario.name,
        request=scenario.request,
        locale=scenario.locale,
        inventory={},
        cookware=[],
        follow_up=scenario.follow_up,
        **entry,
    )


def expectation_failures(
    scenario: Scenario,
    plan: dict[str, Any] | None,
    passed: bool,
    model_calls: int,
    facts: TurnFacts,
    *,
    first_passed: bool = True,
    reply: str = "",
    library_titles: Sequence[str] = (),
    first_plan: dict[str, Any] | None = None,
    check_plan_flag: bool = True,
) -> list[str]:
    """Every expectation of the judged turn: `check_expectations` on its plan, the plan 3b-1
    follow-up keys on `facts`, `expect_any_of` (met when one entry's keys are all met), and
    the locked dishes a revision removed without the scenario expecting it. Pure."""

    def judged(s: Scenario, plan_flag: bool) -> list[str]:
        return [
            *check_expectations(
                s, plan, passed, model_calls, first_passed, reply, library_titles, first_plan,
                check_plan_flag=plan_flag,
            ),
            *follow_up_failures(s, facts),
        ]

    failures = judged(scenario, check_plan_flag)
    if scenario.expect_any_of:
        missed = [
            judged(_any_of_entry(scenario, entry), check_plan_flag and "expect_plan" in entry)
            for entry in scenario.expect_any_of
        ]
        if all(missed):
            details = "; ".join(f"[{i}] " + ", ".join(items) for i, items in enumerate(missed))
            failures.append(f"no entry of expect_any_of met: {details}")
    removed = locked_removed(scenario, facts)
    if removed:
        failures.append(f"locked dishes removed without being named: {removed}")
    return failures


def locale_problem(text: str, locale: str, library_titles: Sequence[str] = ()) -> str | None:
    """Why `text` is not in the reply locale, judged without recipe titles; None when it is.
    Pure."""
    for title in sorted(library_titles, key=len, reverse=True):
        text = text.replace(title, "")
    has_cjk = bool(_CJK.search(text))
    if locale == "en" and has_cjk:
        return "reply is not English (Chinese characters left after removing titles)"
    if locale == "zh" and not has_cjk:
        return "reply is not Chinese (no Chinese characters besides titles)"
    return None


NOTE_FLAGS = ("outside_dish", "counts_dishes", "multi_sentence", "wrong_locale")
# The flags that count toward `flagged_rate`. multi_sentence is still counted in `flags` but
# is reported apart: it measures only the one-sentence rule (user decision).
RATE_FLAGS = ("outside_dish", "counts_dishes", "wrong_locale")
_NUMBER_CHARS = "一二两三四五六七八九十几"
_NUMBER = rf"(?:\d+|[{_NUMBER_CHARS}]+)"
_COUNTED = r"(?:道|个菜|荤|素|dishes|dish|meat|vegetables|vegetable)"
# A number directly before a counted word ("3 道荤菜", "两荤一素", "4 dishes"), not part of a
# longer number and not an ordinal or "the one that" ("第一道菜", "最费时的一道"); a lone 一
# before 道 or 个菜 is one dish, never a count ("这一道菜最费时"); or 荤 / 素
# followed by digits ("荤 4 道"). Words such as "荤素一起" or "this dish 20 minutes" are not
# counts.
_COUNTS_DISHES = re.compile(
    rf"(?<![第的\d{_NUMBER_CHARS}])(?!一\s*(?:道|个菜)){_NUMBER}\s*{_COUNTED}(?![a-z])"
    r"|[荤素]\s*\d+",
    re.IGNORECASE,
)
# A sentence end: 。！？!? or a period that is not a decimal point.
_SENTENCE_END = re.compile(r"[。！？!?]+|\.+(?!\d)")


def note_flags(
    note: str, plan_titles: list[str], library_titles: list[str], locale: str
) -> list[str]:
    """The automatic checks of a plan's model note (design 5.5), in NOTE_FLAGS order. Pure."""
    flags = []
    rest = note
    for title in sorted(plan_titles, key=len, reverse=True):
        rest = rest.replace(title, "")
    if any(t not in plan_titles and t in rest for t in library_titles):
        flags.append("outside_dish")
    if _COUNTS_DISHES.search(note):
        flags.append("counts_dishes")
    if sum(bool(part.strip()) for part in _SENTENCE_END.split(note)) > 1:
        flags.append("multi_sentence")
    if locale_problem(note, locale, library_titles):
        flags.append("wrong_locale")
    return flags


def shown_model_note(reply: str, locale: str, has_plan: bool) -> str:
    """The model's own note in the reply: without the fixed closest-attempt text, and empty
    when no plan is shown (the fixed stop notes are not model text)."""
    if not has_plan:
        return ""
    closest = CLOSEST_NOTE_ZH if locale == "zh" else CLOSEST_NOTE_EN
    if reply == closest:
        return ""
    return reply.removeprefix(closest + "\n\n")


# ---- diners and privacy checks (plan 2b, spec 5.9) ----

_RULE_KINDS = {"restriction": "dietary restriction", "dislike": "dislike"}


def _fold(value: Any) -> str:
    return str(value).strip().casefold()


def _rule_names(rule: dict[str, Any], ingredient_names: dict[int, set[str]]) -> set[str]:
    """Every name a card or context rule answers to: its text, its shown ingredient name,
    and the dictionary names of its ingredient, folded."""
    names = {rule.get("text"), rule.get("ingredient_name")}
    names.update(ingredient_names.get(rule.get("ingredient_id"), set()))
    return {_fold(name) for name in names if name}


def _rule_matches(
    rule: dict[str, Any], wanted: dict[str, Any], ingredient_names: dict[int, set[str]]
) -> bool:
    """A rule matches `wanted` on each of allergen, ingredient (a dictionary name or a text
    rule's wording), text, and kind (restriction | dislike) that `wanted` gives."""
    if "allergen" in wanted and rule.get("allergen") != wanted["allergen"]:
        return False
    for key in ("ingredient", "name", "text"):
        if key in wanted and _fold(wanted[key]) not in _rule_names(rule, ingredient_names):
            return False
    kind = wanted.get("kind")
    return kind is None or rule.get("rule_type") == _RULE_KINDS.get(kind, kind)


def _row_key(row: dict[str, Any]) -> str:
    return "me" if row.get("is_self") else _fold(row.get("name") or "")


def _guest_fits(
    guest: dict[str, Any], wanted: dict[str, Any], ingredient_names: dict[int, set[str]]
) -> bool:
    if "name" in wanted and _fold(guest.get("name") or "") != _fold(wanted["name"] or ""):
        return False
    proposed = guest.get("proposed") or []
    if any(
        not any(r.get("allergen") == allergen for r in proposed)
        for allergen in wanted.get("allergens") or []
    ):
        return False
    if any(
        not any(r.get("allergen") is None and _rule_matches(r, item, ingredient_names)
                for r in proposed)
        for item in wanted.get("avoid") or []
    ):
        return False
    return "diet_words" not in wanted or bool(guest.get("diet_words")) == wanted["diet_words"]


def _match_guests(
    wanted: list[dict[str, Any]],
    guests: list[dict[str, Any]],
    ingredient_names: dict[int, set[str]],
) -> bool:
    """Whether each wanted guest fits a different guest on the card."""
    if not wanted:
        return True
    first, rest = wanted[0], wanted[1:]
    return any(
        _guest_fits(guest, first, ingredient_names)
        and _match_guests(rest, guests[:i] + guests[i + 1:], ingredient_names)
        for i, guest in enumerate(guests)
    )


CARD_KEYS = {"preselected", "others", "nudge", "diet_words", "guests", "self_proposed"}


def card_failures(
    expect: bool | dict[str, Any] | None,
    cards: list[dict[str, Any]],
    ingredient_names: dict[int, set[str]],
) -> list[str]:
    """What about the diner cards did not match `expect_card`. None checks nothing; False
    means no card may appear in any turn; True or a mapping means a card must appear, and
    the mapping's parts are checked against the first card. Pure."""
    if expect is None:
        return []
    if expect is False:
        return [f"a diner card appeared ({len(cards)}), expected none"] if cards else []
    if not cards:
        return ["no diner card appeared"]
    if expect is True:
        return []
    card = cards[0]
    rows = card.get("rows") or []
    guests = card.get("guests") or []
    failures = []
    if "preselected" in expect:
        got = sorted(_row_key(row) for row in rows if row.get("selected"))
        wanted = sorted(_fold(name) for name in expect["preselected"])
        if got != wanted:
            failures.append(f"card preselected {got}, expected {wanted}")
    for key in ("others", "nudge"):
        if key in expect and card.get(key) != expect[key]:
            failures.append(f"card {key} {card.get(key)}, expected {expect[key]}")
    if "diet_words" in expect:
        shown = any(row.get("diet_words") for row in rows) or any(
            guest.get("diet_words") for guest in guests
        )
        if shown != expect["diet_words"]:
            failures.append(f"card diet-word hint {shown}, expected {expect['diet_words']}")
    if "guests" in expect and not _match_guests(expect["guests"], guests, ingredient_names):
        shown = [
            {key: g.get(key) for key in ("name", "proposed", "diet_words")} for g in guests
        ]
        failures.append(f"card guests do not match {expect['guests']}; got {shown}")
    if "self_proposed" in expect:
        me = next((row for row in rows if row.get("is_self")), {})
        proposed = me.get("proposed") or []
        missing = [
            item for item in expect["self_proposed"]
            if not any(_rule_matches(r, item, ingredient_names) for r in proposed)
        ]
        if missing:
            failures.append(f"not proposed on the user's row: {missing}; got {proposed}")
    return failures


def _who(key: str, diner_names: dict[str, str]) -> str:
    if key.startswith("guest:"):
        return "guest"
    return _fold(diner_names.get(key, key))


def rule_failures(
    applied: list[dict[str, Any]],
    not_applied: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    diner_names: dict[str, str],
    ingredient_names: dict[int, set[str]],
) -> list[str]:
    """Check the planning context's rules (RuleRef dicts). `diner_names` maps "diner:<id>"
    to "me" or the friend's name; any "guest:<n>" key is "guest". Pure."""

    def found(wanted: dict[str, Any]) -> bool:
        who = _fold(wanted["who"])
        target = {k: v for k, v in wanted.items() if k != "who"}
        return any(
            _who(rule["diner_key"], diner_names) == who
            and _rule_matches(rule, target, ingredient_names)
            for rule in rules
        )

    failures = [f"rule not applied: {w}" for w in applied if not found(w)]
    failures += [f"rule applied but should not be: {w}" for w in not_applied if found(w)]
    return failures


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _model_bound_texts(messages: Any) -> Iterator[str]:
    """The user-data text of recorded messages: each message's content and its tool calls'
    arguments. System prompts are skipped (fixed text the pre-hook leaves as written, whose
    examples may hold names), as are the record's own fields (type, name, ids)."""
    for message in messages or []:
        if not isinstance(message, dict) or message.get("type") == "system":
            continue
        yield from _strings(message.get("content"))
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                yield from _strings(call.get("args"))


def nickname_failures(
    runs_events: list[list[dict[str, Any]]],
    nicknames: Sequence[str],
    terms: frozenset[str],
) -> list[str]:
    """The `nickname_sent` check: a saved nickname in any model-bound message (the content
    and tool-call arguments of the non-system `messages` of every `model_call` event: route,
    parse, and planner) of any planning run of the thread. An occurrence inside a longer
    food word ("鱼" in "鱼香肉丝") is not naming the friend, the same rule the nickname
    replacement follows. Pure."""
    friends = [Friend(-i, name) for i, name in enumerate(nicknames, start=1)]
    by_name: dict[str, list[str]] = {}
    for number, events in enumerate(runs_events, start=1):
        for event in events:
            if event.get("kind") != "model_call":
                continue
            for text in _model_bound_texts(event.get("messages")):
                for _, _, friend in find_friends(text, friends, terms):
                    where = f"run {number} {event.get('step', 'planner')}"
                    places = by_name.setdefault(friend.nickname, [])
                    if where not in places:
                        places.append(where)
    return [f"nickname sent: {name} in {', '.join(places)}" for name, places in by_name.items()]


# A "Diner N" label in text shown to the user, also without a space ("Diner2"). An ASCII
# lookbehind instead of \b, so "和Diner 2" (no \b between CJK and Latin) also counts.
LABEL_SHOWN = re.compile(r"(?<![A-Za-z0-9])diner\s*\d+", re.IGNORECASE)


def label_failures(texts: Sequence[str]) -> list[str]:
    """The `label_shown` check over the texts shown to the user. Pure."""
    found = list(dict.fromkeys(m.group(0) for t in texts for m in LABEL_SHOWN.finditer(t)))
    return [f"label shown: {found}"] if found else []


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


def load_scenarios(path: Path, library_titles: Collection[str] | None = None) -> list[Scenario]:
    """Read and check the scenarios. With `library_titles`, every title a follow-up key names
    must be a library dish (`check_titles`)."""
    scenarios = []
    for item in _read_yaml(path, "scenarios"):
        try:
            scenario = Scenario(**item)
        except TypeError as error:
            raise ValueError(f"Bad scenario {item.get('name', '?')}: {error}") from error
        if scenario.locale not in ("zh", "en"):
            raise ValueError(f"Scenario {scenario.name}: locale must be zh or en")
        _check_diner_keys(scenario)
        check_revise_keys(scenario, ANY_OF_KEYS)
        for entry in scenario.expect_any_of:
            # The entry's own values are checked as a scenario's (unknown values refused).
            check_revise_keys(_any_of_entry(scenario, entry), ANY_OF_KEYS)
        scenarios.append(scenario)
    if library_titles is not None:
        check_titles(scenarios, library_titles)
    return scenarios


def check_titles(scenarios: Sequence[Scenario], library_titles: Collection[str]) -> None:
    """Refuse a follow-up key that names a dish the library does not hold."""
    for scenario in scenarios:
        problems = title_problems(scenario, library_titles)
        if problems:
            raise ValueError(f"Scenario {scenario.name}: not library titles: {problems}")


DINERS_ANSWER_KEYS = {"selected", "guests", "others", "others_answer"}


def _check_diner_keys(scenario: Scenario) -> None:
    """Refuse a plan 2b key with a value the runner would not understand."""

    def bad(what: str) -> ValueError:
        return ValueError(f"Scenario {scenario.name}: {what}")

    if scenario.request_style not in REQUEST_STYLES:
        raise bad(f"request_style must be one of {list(REQUEST_STYLES)}")
    answer = scenario.diners_answer
    if isinstance(answer, str) and answer != "accept":
        raise bad('diners_answer must be "accept" or a mapping')
    if isinstance(answer, dict):
        if set(answer) - DINERS_ANSWER_KEYS:
            raise bad(f"diners_answer keys must be among {sorted(DINERS_ANSWER_KEYS)}")
        if answer.get("others_answer") not in (None, "none", "unsure"):
            raise bad("diners_answer.others_answer must be none or unsure")
    if isinstance(scenario.expect_card, dict) and set(scenario.expect_card) - CARD_KEYS:
        raise bad(f"expect_card keys must be among {sorted(CARD_KEYS)}")
    if scenario.between_turns and not scenario.follow_up:
        raise bad("between_turns runs before the follow-up, so it needs follow_up")
    for action in scenario.between_turns:
        if not isinstance(action, dict) or set(action) != {"confirm_allergens"}:
            raise bad("between_turns items must be {confirm_allergens: {name, allergens}}")


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


def _rule_row(session: Session, diner_id: int, item: dict[str, Any]) -> DietaryRule:
    ingredient = item.get("ingredient")
    return DietaryRule(
        diner_id=diner_id,
        rule_type=item["type"],
        allergen=item.get("allergen"),
        ingredient_id=_one(session, Ingredient, ingredient).id if ingredient else None,
        text=item.get("text"),
    )


def reset_scenario_state(session: Session, scenario: Scenario) -> None:
    """Make inventory, owned cookware, the user's rules, the saved friends (deleted and
    created again, with their rules), and the request style the scenario's own. The user's
    own diner row is kept. What an earlier scenario's `between_turns` did is undone: the
    dictionary entries it created (the only user entries in an evaluation) are removed and
    the recipe lines it linked to them are unlinked again."""
    created = select(Ingredient.id).where(Ingredient.source == "user")
    session.execute(
        update(RecipeIngredient)
        .where(RecipeIngredient.ingredient_id.in_(created))
        .values(ingredient_id=None)
        .execution_options(synchronize_session=False)
    )
    session.execute(delete(Ingredient).where(Ingredient.source == "user"))
    session.execute(delete(Inventory))
    session.execute(delete(CookwareOwned))
    session.execute(delete(DietaryRule))
    session.execute(delete(Diner).where(Diner.is_self.is_(False)))
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
        session.add(_rule_row(session, diner_id, item))
    for friend in scenario.friends:
        diner = Diner(display_name=friend["name"], is_self=False)
        session.add(diner)
        session.flush()
        for item in friend.get("rules") or []:
            session.add(_rule_row(session, diner.id, item))
    session.commit()
    set_request_style(session, scenario.request_style)


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


def _proposed_choice(rule: dict[str, Any]) -> RuleChoice:
    """A rule proposed on the card, ticked for this meal only."""
    return RuleChoice(
        rule_type=rule["rule_type"],
        allergen=rule.get("allergen"),
        ingredient_id=rule.get("ingredient_id"),
        text=rule.get("text"),
    )


def _scenario_choice(item: dict[str, Any], ingredient_id: Callable[[str], int]) -> RuleChoice:
    """A rule in the scenario format ({type, allergen?, ingredient?, text?}), meal only."""
    ingredient = item.get("ingredient")
    return RuleChoice(
        rule_type=item["type"],
        allergen=item.get("allergen"),
        ingredient_id=ingredient_id(ingredient) if ingredient else None,
        text=item.get("text"),
    )


def diner_answer(
    card: dict[str, Any],
    how: str | dict[str, Any] | None,
    ingredient_id: Callable[[str], int],
) -> DinerAnswer:
    """The runner's answer to a diner card. The default ("accept", or no `diners_answer`)
    keeps the card's preselection, ticks every proposed rule of the selected rows and of the
    guests for this meal only, and answers "none" for the unmatched people. A mapping
    replaces single parts: `selected` (names, "me"; each selected row's proposed rules are
    ticked), `guests` ([{name, rules, save}]), `others`, and `others_answer`.

    Raises ValueError for a selected name that is not on the card."""
    how = {} if how in (None, "accept") else dict(how)
    rows = card.get("rows") or []
    if "selected" in how:
        by_key = {_row_key(row): row for row in rows}
        missing = [name for name in how["selected"] if _fold(name) not in by_key]
        if missing:
            raise ValueError(f"diners_answer selects people not on the card: {missing}")
        chosen = [by_key[_fold(name)] for name in how["selected"]]
    else:
        chosen = [row for row in rows if row.get("selected")]
    stated = {
        row["diner_id"]: [_proposed_choice(rule) for rule in row["proposed"]]
        for row in chosen
        if row.get("proposed")
    }
    if "guests" in how:
        guests = [
            GuestAnswer(
                name=guest.get("name"),
                rules=[_scenario_choice(rule, ingredient_id) for rule in guest.get("rules") or []],
                save=guest.get("save", False),
            )
            for guest in how["guests"]
        ]
    else:
        guests = [
            GuestAnswer(
                name=guest.get("name"),
                rules=[_proposed_choice(rule) for rule in guest.get("proposed") or []],
            )
            for guest in card.get("guests") or []
        ]
    others = int(how.get("others", card.get("others") or 0))
    return DinerAnswer(
        selected=[row["diner_id"] for row in chosen],
        stated=stated,
        guests=guests,
        others=others,
        others_answer=how.get("others_answer", "none") if others else None,
    )


# More questions than this in one turn means the conversation is going in circles.
MAX_ANSWERS_PER_TURN = 8
# _drive's note when a turn stopped at a question the scenario gives no answer for.
QUESTION_STOP = "stopped at a question: question"


def _drive(
    graph,
    factory: sessionmaker[Session],
    config: dict[str, Any],
    scenario: Scenario,
    text: str,
    answers: list[str],
    cards: list[dict[str, Any]],
    choices: list[dict[str, Any]] | None = None,
) -> str:
    """Send one user message and answer what the graph asks, the way the chat API resumes:
    the diner card from `diners_answer` (every card shown is added to `cards`), the choice
    card from `choice_answer` (every one shown is added to `choices`), and other questions
    with the next of `answers` (consumed). Returns "" when the turn ended, CHOICE_STOP when
    it stopped at a choice card the scenario does not answer, or a note when it stopped at a
    question the scenario does not answer."""
    graph.invoke({"messages": [HumanMessage(text)], "locale": scenario.locale}, config)
    for _ in range(MAX_ANSWERS_PER_TURN):
        pending = graph.get_state(config).interrupts
        if not pending:
            return ""
        value = pending[0].value
        kind = value.get("kind") if isinstance(value, dict) else None
        if kind == "diners":
            cards.append(value)
            with factory() as session:
                answer = diner_answer(
                    value, scenario.diners_answer, lambda name: _one(session, Ingredient, name).id
                )
            resume: Any = answer.model_dump(mode="json")
        elif kind == "revise_or_plan":
            if choices is not None:
                choices.append(value)
            if scenario.choice_answer is None:
                return CHOICE_STOP
            resume = scenario.choice_answer
        elif kind == "question" and answers:
            resume = answers.pop(0)
        else:
            return f"stopped at a question: {kind}"
        # By interrupt id, as the chat API does.
        graph.invoke(Command(resume={pending[0].id: resume}), config)
    return "stopped: too many questions in one turn"


def run_between_turns(factory: sessionmaker[Session], actions: list[dict[str, Any]]) -> None:
    """Database actions before the follow-up. `confirm_allergens` confirms the allergens of
    an unmatched recipe line's name the way the dictionary API does: a new user entry with
    those allergens, and matching unlinked lines linked to it. The next scenario's reset
    undoes it, so a name some entry already owns is refused (that change could not be undone
    exactly)."""
    with factory() as session:
        for action in actions:
            item = action["confirm_allergens"]
            if find_term_owner(session, Ingredient, item["name"]) is not None:
                raise ValueError("confirm_allergens takes only an unmatched line's name")
            resolve_allergens(session, item["name"], None, list(item.get("allergens") or []))
        session.commit()


def _diner_failures(
    session: Session,
    scenario: Scenario,
    state: dict[str, Any],
    cards: list[dict[str, Any]],
    runs_events: list[list[dict[str, Any]]],
) -> list[str]:
    """The diner expectations and the two privacy checks, read after the scenario ran."""
    rules: list[dict[str, Any]] = []
    data = state.get("constraints")
    if data:
        constraints = MealConstraints(
            diner_ids=tuple(data["diner_ids"]),
            guest_rules=tuple(RuleRef(**rule) for rule in data.get("guest_rules") or []),
            unconfirmed_diners=data["unconfirmed_diners"],
        )
        rules = [asdict(rule) for rule in load_planning_context(session, constraints).rules]
    diner_names = {
        f"diner:{diner.id}": "me" if diner.is_self else diner.display_name
        for diner in session.scalars(select(Diner))
    }
    ids = {rule.get("ingredient_id") for rule in rules}
    for card in cards:
        for person in [*(card.get("rows") or []), *(card.get("guests") or [])]:
            ids.update(rule.get("ingredient_id") for rule in person.get("proposed") or [])
    ids.discard(None)
    ingredient_names = {
        entry.id: set(all_terms(entry))
        for entry in session.scalars(select(Ingredient).where(Ingredient.id.in_(ids)))
    }
    shown = [
        str(m.content)
        for m in state.get("messages") or []
        if isinstance(m, AIMessage) and isinstance(m.content, str)
    ]
    shown += [text for card in cards for text in _strings(card)]
    shown += list(_strings(state.get("current_plan")))
    return [
        *card_failures(scenario.expect_card, cards, ingredient_names),
        *rule_failures(
            scenario.expect_rules_applied,
            scenario.expect_rules_not_applied,
            rules,
            diner_names,
            ingredient_names,
        ),
        *nickname_failures(
            runs_events, [friend["name"] for friend in scenario.friends], food_terms(session)
        ),
        *label_failures(shown),
    ]


def _shown_plan(values: dict[str, Any]) -> dict[str, Any] | None:
    """The newest reply's plan card: on a revision that changed nothing it shows the dishes
    with what stopped it, while the current plan stays. The current plan when no reply has a
    card."""
    try:
        reply_id = newest_reply_id(values)
    except EditError:
        return values.get("current_plan")
    return values["reply_plans"][reply_id]


def run_scenario(
    graph,
    factory: sessionmaker[Session],
    recorder: PlanningRecorder,
    scenario: Scenario,
    prices: dict[tuple[str, str], tuple[Decimal, Decimal]] | None = None,
    client: LLMClient | None = None,
    library_titles: Sequence[str] = (),
) -> RunOutcome:
    """Run one scenario on a fresh thread and judge its last turn: the follow-up when the
    scenario has one, else the first message. With a follow-up, the first turn's plan may be
    replaced (`first_plan`) and edited by hand (`edits_between_turns`) before it."""
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
    first_plan = None
    cards: list[dict[str, Any]] = []
    # The choice cards of the judged turn.
    choices: list[dict[str, Any]] = []
    answers = list(scenario.answers)
    # Set once the follow-up is sent: the plan before it, its version count, and the last
    # planning run before it (the follow-up's runs come after).
    follow_up_sent = False
    before: dict[str, Any] | None = None
    versions_before = 0
    run_mark = 0
    # The last llm_calls row before the follow-up: the follow-up turn's calls come after.
    call_mark = watermark
    asked = False
    setup_failed = False
    hidden = None
    try:
        if scenario.library_exclude:
            hidden = hide_dishes(factory, scenario.library_exclude)
        note = _drive(
            graph, factory, config, scenario, scenario.request, answers, cards,
            None if scenario.follow_up else choices,
        )
        asked = note == QUESTION_STOP
        if not note and scenario.follow_up:
            # expect_first_reasons judges the model's own first plan, before first_plan
            # replaces its dishes.
            first_plan = graph.get_state(config).values.get("current_plan")
            if scenario.first_plan:
                replace_first_plan(graph, factory, config, scenario.first_plan, scenario.locale)
            run_between_turns(factory, scenario.between_turns)
            apply_edits(graph, factory, config, scenario.edits_between_turns, scenario.locale)
            values = graph.get_state(config).values
            before = values.get("current_plan")
            versions_before = len(values.get("plan_versions") or [])
            with factory() as session:
                run_mark = session.scalar(
                    select(func.coalesce(func.max(PlanningRun.id), 0)).where(
                        PlanningRun.thread_id == thread_id
                    )
                )
                call_mark = session.scalar(select(func.coalesce(func.max(LlmCall.id), 0)))
            follow_up_sent = True
            note = _drive(
                graph, factory, config, scenario, scenario.follow_up, answers, cards, choices
            )
            asked = note == QUESTION_STOP
            if note:
                note = note.replace("at a question", "at a question after the follow-up")
                note = note.replace("at the choice card", "at the choice card after the follow-up")
    except FollowUpSetupError as error:
        # The follow-up was never sent: no turn to rate (turn "none"), never as expected.
        note = f"failed: {error}"
        setup_failed = True
    except Exception as error:
        # Includes AllModelsFailed and MonthlyCapExceeded: record it and go on.
        note = f"failed: {type(error).__name__}"
    finally:
        if hidden is not None:
            bring_back(factory, hidden)
    choice_stop = note.startswith(CHOICE_STOP)
    state = graph.get_state(config).values
    # Only the judged turn's own run is failed: a follow-up that stopped before its run
    # leaves the first turn's record as it was.
    if note and not choice_stop and (state.get("run_id") or 0) > run_mark:
        recorder.fail(state["run_id"], state.get("retries") or 0)
    with factory() as session:
        # With a follow-up the thread has a run per message, in id order; the judged turn's
        # last one is judged, and every one is saved so a first-turn mismatch can be
        # investigated.
        runs = session.scalars(
            select(PlanningRun)
            .where(PlanningRun.thread_id == thread_id)
            .order_by(PlanningRun.id)
        ).all()
        turn_runs = [r for r in runs if r.id > run_mark]
        run = turn_runs[-1] if turn_runs else None
        turn_events = [list(r.events or []) for r in turn_runs]
        all_rows = session.scalars(
            select(LlmCall).where(LlmCall.id > watermark).order_by(LlmCall.id)
        ).all()
        rows = [r for r in all_rows if r.task == EVAL_TASK]
        turn_rows = [r for r in rows if r.id > call_mark]
        embedding_calls = sum(r.task == "embeddings" for r in all_rows)
        record = {
            "planning_run": _run_row(run),
            "planning_runs": [_run_row(r) for r in runs],
            "llm_calls": [_call_row(r) for r in all_rows],
        }
        diner_checks = _diner_failures(session, scenario, state, cards, [r.events for r in runs])
    if setup_failed:
        # The judged turn never ran; the first turn's run is not judged in its place.
        turn = "none"
        run = None
    elif run is None:
        turn = "none" if follow_up_sent else "plan"
    elif (run.request or {}).get("revision") is not None:
        turn = "revision"
    else:
        turn = "plan"
    route = route_of(turn_events, state) if follow_up_sent or not scenario.follow_up else None
    if follow_up_sent:
        versions_now = len(state.get("plan_versions") or [])
        facts = TurnFacts(
            turn=turn,
            route=route,
            choice_card=bool(choices),
            question=versions_now == versions_before
            and (asked or (turn == "revision" and asked_back(state))),
            before=plan_dishes(before),
            after=plan_dishes(state.get("current_plan")),
            locked_before=locked_of(before),
        )
    else:
        facts = TurnFacts(turn=turn, route=route, choice_card=bool(choices), question=asked)
    plan = _shown_plan(state)
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
    failures = expectation_failures(
        scenario,
        plan,
        passed,
        len(rows),
        facts,
        first_passed=first_passed,
        reply=reply,
        library_titles=library_titles,
        first_plan=first_plan,
        # A follow-up that ended without a planning run (an unanswered choice card, an
        # "other" reply) has no plan to pass or fail.
        check_plan_flag=not (turn == "none" and (not note or choice_stop)),
    )
    failures += diner_checks
    if setup_failed:
        failures.append(f"follow-up not sent: {note}")
    titles = [r["title"] for r in (plan or {}).get("recipes", [])]
    # The note checks are for the planner's own note; a revision's reply starts with
    # code-written text naming the removed dishes.
    model_note = shown_model_note(reply, scenario.locale, bool(titles)) if turn == "plan" else ""
    flags = note_flags(model_note, titles, list(library_titles), scenario.locale)
    return RunOutcome(
        scenario=scenario.name,
        first_passed=first_passed,
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
        titles=titles,
        cache_write_input_tokens=sum(r.cache_write_input_tokens or 0 for r in rows),
        embedding_calls=embedding_calls,
        wall_ms=int((time.perf_counter() - started) * 1000),
        call_latencies=[r.latency_ms or 0 for r in rows],
        record=record,
        reply_note=model_note,
        note_flags=flags if model_note.strip() else [],
        note_checked=bool(model_note.strip()),
        card_shown=bool(cards),
        cards_shown=len(cards),
        card=cards[0] if cards else None,
        turn=turn,
        route_intent=facts.route,
        route_as_expected=(
            None if scenario.expect_route is None else facts.route == scenario.expect_route
        ),
        locked_removed=len(locked_removed(scenario, facts)),
        changed=len(facts.gone()),
        deferred_to=scenario.deferred_to,
        turn_model_calls=len(turn_rows),
        follow_up=bool(scenario.follow_up),
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

    Refuses before touching anything when the database already holds recipes or ingredients,
    or when a follow-up key names a dish the library does not hold.
    """
    check_titles(scenarios, [dish["title"] for dish in library])
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
    if outcome.turn != "plan" or outcome.route_intent is not None:
        print(
            f"  turn: {outcome.turn}; route: {outcome.route_intent}; "
            f"changed: {outcome.changed}; locked removed: {outcome.locked_removed}; "
            f"turn model calls: {outcome.turn_model_calls}"
            + (f"; deferred to {outcome.deferred_to}" if outcome.deferred_to else "")
        )
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
    if s["revisions"]:
        print(f"Revisions ({s['revisions']}; planning rates above are over new plans only)")
        print(f"  pass rate:               {s['revision_pass_rate']:.2f}")
        print(f"  locked dishes removed:   {s['locked_removed']}  (target 0)")
        print(f"  dishes changed (avg):    {s['avg_changed']:.2f}")
        print(
            f"  model calls per turn:    {s['avg_turn_model_calls']:.1f} avg, "
            f"{s['max_turn_model_calls']} max"
        )
    if s["route_as_expected"] != "0/0":
        print(f"  route as expected (follow-ups):      {s['route_as_expected']}")
    if s["route_first_as_expected"] != "0/0":
        print(f"  route as expected (first messages):  {s['route_first_as_expected']}")
    if s["deferred"] != "0/0":
        print(f"  deferred (not rated):    {s['deferred']} as expected")
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
        REVISE_TOOLS_VERSION,
        TOOLS_VERSION,
    )
    from minemydinner.agent.request import PROMPT_VERSION, PROMPT_VERSIONS, prompt_files
    from minemydinner.planning import shortlist

    eval_task = client.config.tasks.get(EVAL_TASK)
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "conditions": {
            "git_commit": git_commit,
            # The catalog model the planning_eval task ran on: the one named by
            # --planning-model, else the configured default.
            "planning_model": (
                client.config.models[eval_task.primary].model if eval_task else None
            ),
            "scenarios_file": _file_info(scenarios_path),
            "library_file": _file_info(library_path),
            "prompt_version": PROMPT_VERSION,
            "prompt_versions": dict(PROMPT_VERSIONS),
            "prompt_files": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in prompt_files()
            },
            "tools_version": TOOLS_VERSION,
            "revise_tools_version": REVISE_TOOLS_VERSION,
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
    started: datetime,
    scenarios_path: Path,
    no_shortlist: bool,
    with_embeddings: bool,
    planning_model: str | None = None,
) -> str:
    stamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = scenarios_path.stem.removeprefix("scenarios_")
    shortlist_part = "no-shortlist" if no_shortlist else "shortlist"
    vectors_part = "vectors" if with_embeddings else "no-vectors"
    run_id = f"{stamp}-{name}-{shortlist_part}-{vectors_part}"
    return f"{run_id}-{planning_model}" if planning_model else run_id


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
    "set; compare same-named scenarios across different sets. Outcomes (first and final pass "
    "rate, retries) are over new plans only; revisions (the judged turn ran the revise step) "
    "have their own pass rate, locked dishes removed (target 0), dishes changed and model "
    "calls per revise turn (turn_model_calls: that turn's calls only). Route accuracy is "
    "reported for follow-ups (route_as_expected) and first messages apart. Deferred "
    "scenarios (deferred_to) are left out of every rate and as_expected, and counted in "
    "`deferred`; their cost, calls, tokens and latency count."
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
    checked = [o for o in runs if o.note_checked]
    return {
        "scenarios": n,
        "outcomes": {
            "first_pass_rate": s["first_pass_rate"],
            "final_pass_rate": s["final_pass_rate"],
            "as_expected": s["as_expected"],
            "avg_retries": s["avg_retries"],
            "deferred": s["deferred"],
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
        "revisions": {
            "turns": s["revisions"],
            "pass_rate": s["revision_pass_rate"],
            "locked_removed": s["locked_removed"],
            "avg_changed": s["avg_changed"],
            "avg_turn_model_calls": s["avg_turn_model_calls"],
            "max_turn_model_calls": s["max_turn_model_calls"],
            "route_as_expected": s["route_as_expected"],
            "route_first_as_expected": s["route_first_as_expected"],
        },
        "notes": {
            "checked": len(checked),
            "flags": {f: sum(f in o.note_flags for o in checked) for f in NOTE_FLAGS},
            "flagged_rate": _rate(
                sum(bool(set(o.note_flags) & set(RATE_FLAGS)) for o in checked), len(checked)
            ),
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
        item.pop("reply_note")
        item.pop("card")
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
    """The model-facing surface: the planner's tools (the revise step uses the same ones), each
    step's final-answer schema, and the tools version each step's records carry."""
    from minemydinner.agent.planner import (
        REVISE_TOOLS_VERSION,
        TOOLS_VERSION,
        PlanProposal,
    )
    from minemydinner.agent.revision import RevisionProposal
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
        "revise_final_answer": {
            "name": "RevisionProposal",
            "schema": RevisionProposal.model_json_schema(),
        },
        "tools_version": TOOLS_VERSION,
        "revise_tools_version": REVISE_TOOLS_VERSION,
    }


def _dump(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, default=str)


def write_run(
    root: Path, run_id: str, summary: dict[str, Any], outcomes: list[RunOutcome]
) -> Path:
    """Write the run folder: summary.json, results.jsonl (git-ignored), tools.json, and the
    prompt files in use. Returns the folder."""
    from minemydinner.agent.request import prompt_files

    folder = root / run_id
    (folder / "prompts").mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(_dump(summary, indent=2), encoding="utf-8")
    (folder / "tools.json").write_text(_dump(_tools_document(), indent=2), encoding="utf-8")
    lines = [_dump({**_entry(o), **o.record}) for o in outcomes]
    (folder / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for prompt in prompt_files():
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
    parser.add_argument(
        "--planning-model",
        help="run the whole planning_eval task (route, parse, planner) on this tool-calling "
        "catalog model, with no fallback",
    )
    args = parser.parse_args(argv)
    if args.planning_model:
        try:
            models = tool_calling_models()
        except ConfigError as error:
            print(error, file=sys.stderr)
            return 2
        if args.planning_model not in models:
            print(
                f"Unknown planning model {args.planning_model}; tool-calling catalog models: "
                + ", ".join(models),
                file=sys.stderr,
            )
            return 2
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
        check_titles(scenarios, [dish["title"] for dish in library])
    except (UnsafeDatabase, InputError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    llm_config = load_llm_config(model_overrides=settings.model_overrides())
    if args.planning_model:
        llm_config = pin_task_model(llm_config, EVAL_TASK, args.planning_model)
    client = LLMClient(
        llm_config,
        factory,
        settings.llm_monthly_cap_usd,
        pre_hooks=(replace_nicknames,),
        post_hooks=(restore_labels,),
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
                    started,
                    args.scenarios,
                    args.no_shortlist,
                    args.with_embeddings,
                    args.planning_model,
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
