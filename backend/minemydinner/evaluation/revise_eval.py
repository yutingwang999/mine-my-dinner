"""Follow-up scenarios of the planning evaluation (plan 3b-1 spec 5.6): a fixed first plan,
3a hand edits between the turns, the choice card's answer, dishes hidden for one scenario,
and the follow-up's own checks (route, choice card, question, which dishes changed, locked
dishes kept).

`planning.py` wires these into `load_scenarios`, `run_scenario`, and the summary. Nothing here
imports `planning.py`, so the scenario is typed loosely (any object with the Scenario fields).
"""

import re
from collections.abc import Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage
from sqlalchemy import Table, delete, insert, select
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.agent.actions import ACTIONS
from minemydinner.agent.edits import (
    Add,
    EditError,
    Keep,
    Remove,
    apply_edit,
    locked_ids,
    newest_reply_id,
)
from minemydinner.agent.graph import WISH_UNMET_NOTE_EN, WISH_UNMET_NOTE_ZH
from minemydinner.agent.labels import LabelTable, labels_scope, load_label_context
from minemydinner.agent.plan_card import build_dishes_card, card_base, meal_constraints
from minemydinner.agent.request import ParsedRequest
from minemydinner.agent.revise_step import WISH_UNMET, open_conflict_ids
from minemydinner.agent.revision import asks_back
from minemydinner.models import Diner, Rating, Recipe, RecipeCookware, RecipeImage, RecipeIngredient
from minemydinner.planning.context import load_planning_context
from minemydinner.planning.validate import PlanSpec, validate_plan

# The values the scenario keys take.
ROUTES = ("plan", "revise", "unsure")
CHOICE_ANSWERS = ("revise", "plan")
EDIT_KINDS = {"add": Add, "remove": Remove, "keep": Keep}
# Keys that set up or judge the plan before and after a follow-up, so they need one.
FOLLOW_UP_KEYS = (
    "first_plan",
    "edits_between_turns",
    "expect_unchanged",
    "expect_removed",
    "expect_max_changed",
    "expect_removed_any",
    "expect_follow_up_diner_card",
)
# expect_* keys an expect_any_of entry may not hold: the list itself, and the diner checks,
# which are judged once over the whole scenario.
NOT_IN_ANY_OF = ("expect_any_of", "expect_card", "expect_rules_applied", "expect_rules_not_applied")
# _drive's note when the follow-up stopped at the choice card because the scenario gives no
# answer: an expected end of the turn, not a failure.
CHOICE_STOP = "stopped at the choice card"
# The judged turn: "plan" (planned from scratch), "revision" (the revise step ran), or "none"
# (the follow-up made no planning run: an unanswered choice card, an "other" reply).
TURN_KINDS = ("plan", "revision", "none")
# The tables a hidden dish's rows live in, parent first.
_RECIPE_TABLES: tuple[Table, ...] = (
    Recipe.__table__,
    RecipeIngredient.__table__,
    RecipeCookware.__table__,
    RecipeImage.__table__,
    Rating.__table__,
)


class FollowUpSetupError(Exception):
    """A follow-up scenario that cannot be set up (an edit refused, a title not found). The
    message holds only the scenario's own words, so it is safe to print."""


# ---- loading ----


def _titles_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(t, str) and t.strip() for t in value)


def check_revise_keys(scenario: Any, any_of_keys: Collection[str]) -> None:
    """Refuse a plan 3b-1 key with a value the runner would not understand. `any_of_keys` are
    the keys an expect_any_of entry may hold. Raises ValueError."""

    def bad(what: str) -> ValueError:
        return ValueError(f"Scenario {scenario.name}: {what}")

    for key in ("first_plan", "library_exclude", "expect_removed", "expect_removed_any"):
        if not _titles_list(getattr(scenario, key)):
            raise bad(f"{key} must be a list of titles")
    if not scenario.follow_up:
        for key in FOLLOW_UP_KEYS:
            value = getattr(scenario, key)
            if value not in (None, [], ()):
                raise bad(f"{key} judges or changes the plan before a follow-up, so it needs follow_up")
    edits = scenario.edits_between_turns
    if not isinstance(edits, list):
        raise bad("edits_between_turns must be a list")
    for item in edits:
        if (
            not isinstance(item, dict)
            or len(item) != 1
            or next(iter(item)) not in EDIT_KINDS
            or not _titles_list([next(iter(item.values()))])
        ):
            raise bad("edits_between_turns items must be {add: title}, {remove: title} or {keep: title}")
    if scenario.choice_answer not in (None, *CHOICE_ANSWERS):
        raise bad(f"choice_answer must be one of {list(CHOICE_ANSWERS)}")
    if scenario.expect_route not in (None, *ROUTES):
        raise bad(f"expect_route must be one of {list(ROUTES)}")
    for key in (
        "expect_choice_card",
        "expect_question",
        "expect_unchanged",
        "expect_wish_unmet",
        "expect_no_question",
        "expect_follow_up_diner_card",
    ):
        value = getattr(scenario, key)
        if value is not None and not isinstance(value, bool):
            raise bad(f"{key} must be true or false")
    if scenario.button_after is not None and scenario.button_after not in ACTIONS:
        raise bad(f"button_after must be one of {list(ACTIONS)}")
    kept = scenario.expect_request_kept
    if not isinstance(kept, list) or not all(
        isinstance(name, str) and name in ParsedRequest.model_fields for name in kept
    ):
        raise bad("expect_request_kept must be a list of request field names")
    limit = scenario.expect_max_changed
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise bad("expect_max_changed must be a whole number of dishes, 0 or more")
    if scenario.deferred_to is not None and (
        not isinstance(scenario.deferred_to, str) or not scenario.deferred_to.strip()
    ):
        raise bad("deferred_to must name a plan")
    entries = scenario.expect_any_of
    if not isinstance(entries, list):
        raise bad("expect_any_of must be a list")
    for entry in entries:
        if not isinstance(entry, dict) or not entry:
            raise bad("expect_any_of items must be non-empty mappings of expect_* keys")
        unknown = sorted(set(entry) - set(any_of_keys))
        if unknown:
            raise bad(f"expect_any_of keys must be expect_* keys, not {unknown}")


def removal_titles(scenario: Any) -> set[str]:
    """Every title the scenario expects to leave: expect_removed, also inside expect_any_of
    entries. A locked dish among them is allowed to go."""
    titles = set(scenario.expect_removed)
    for entry in scenario.expect_any_of:
        titles.update(entry.get("expect_removed") or [])
    return titles


def title_problems(scenario: Any, library_titles: Collection[str]) -> list[str]:
    """The titles a follow-up key names that the library does not hold (the runner needs the
    first ones; a typo in the others would pass unseen)."""
    known = set(library_titles)
    named: list[tuple[str, str]] = [(t, "first_plan") for t in scenario.first_plan]
    named += [(next(iter(e.values())), "edits_between_turns") for e in scenario.edits_between_turns]
    named += [(t, "library_exclude") for t in scenario.library_exclude]
    named += [(t, "expect_removed") for t in scenario.expect_removed]
    named += [(t, "expect_removed_any") for t in scenario.expect_removed_any]
    for entry in scenario.expect_any_of:
        for key in ("expect_removed", "expect_removed_any"):
            named += [(t, f"expect_any_of.{key}") for t in entry.get(key) or []]
    return [f"{key}: {title}" for title, key in named if title not in known]


# ---- running ----


@contextmanager
def _labels(session: Session, values: dict, locale: str) -> Iterator[None]:
    """The thread's label table as the label context, as the edit API sets it."""
    self_id = session.scalar(select(Diner.id).where(Diner.is_self))
    table = LabelTable.from_state(values.get("diner_labels"), self_id)
    with labels_scope(load_label_context(session, table, locale)):
        yield


def _recipe_ids(session: Session, titles: Sequence[str]) -> list[int]:
    rows = dict(session.execute(select(Recipe.title, Recipe.id).where(Recipe.title.in_(titles))).all())
    missing = [t for t in titles if t not in rows]
    if missing:
        raise FollowUpSetupError(f"not in the library: {missing}")
    return [rows[t] for t in titles]


# The text of the reply whose dishes `first_plan` replaced. In the app a plan reply's text is
# the planner's note about the dishes on its card; the planner's note named its own dishes, so
# it is replaced by a plain sentence naming the fixed plan's dishes. Later turns' route and
# parse then read the plan the scenario set, as they would read a real reply (development
# run 2 of plan 3b-1b: a rule about lamb was routed "other" because the stale reply named no
# lamb dish).
FIRST_PLAN_REPLY = {"zh": "这顿做：{dishes}。", "en": "This meal: {dishes}."}


def first_plan_reply(titles: Sequence[str], locale: str) -> str:
    """The fixed first plan's reply text, in the scenario's locale."""
    if locale == "zh":
        return FIRST_PLAN_REPLY["zh"].format(dishes="、".join(titles))
    return FIRST_PLAN_REPLY["en"].format(dishes=", ".join(titles))


def replace_first_plan(
    graph, factory: sessionmaker[Session], config: dict, titles: Sequence[str], locale: str
) -> None:
    """Replace the newest reply's dishes with `titles` as a model version: the plan card of
    those dishes checked against the first request (not edited, no locks), in place of the
    first turn's model version, written as `present_plan` would. The reply's text is replaced
    too (same message id), by `first_plan_reply`, so it names the dishes on its card."""
    values = graph.get_state(config).values
    try:
        reply_id = newest_reply_id(values)
    except EditError:
        raise FollowUpSetupError("first_plan: the first message gave no plan reply") from None
    with factory() as session:
        ids = _recipe_ids(session, titles)
        with _labels(session, values, locale):
            ctx = load_planning_context(session, meal_constraints(values))
            result = validate_plan(session, ctx, PlanSpec.model_validate(values["spec"]), ids)
            card = build_dishes_card(
                session,
                ctx,
                recipe_ids=ids,
                validation=result.model_dump(mode="json"),
                base=card_base(values, session),
                locale=locale,
            )
    card["locked"] = []
    # The first turn's request, as present_plan keeps it on the card, so a next-action button
    # right after the seeded plan changes it in code (plan 3b-1b spec 5.5).
    card["request"] = values.get("request")
    versions = list(values.get("plan_versions") or [])
    if versions:
        versions[-1] = card
    else:
        versions.append(card)
    update = {
        "current_plan": card,
        "plan_versions": versions,
        "reply_plans": {**(values.get("reply_plans") or {}), reply_id: card},
        # Same id: add_messages replaces the reply in place.
        "messages": [AIMessage(first_plan_reply(titles, locale), id=reply_id)],
    }
    graph.update_state(config, update, as_node="present_plan")


def apply_edits(
    graph, factory: sessionmaker[Session], config: dict, edits: list[dict], locale: str
) -> None:
    """The 3a edits in order, through `apply_edit` and `update_state` as the edit API applies
    them. An add or keep that needs confirmation is confirmed with every conflict it lists."""
    for item in edits:
        kind, title = next(iter(item.items()))
        values = graph.get_state(config).values
        with factory() as session:
            [recipe_id] = _recipe_ids(session, [title])
            action = EDIT_KINDS[kind](recipe_id=recipe_id)
            with _labels(session, values, locale):
                ctx = load_planning_context(session, meal_constraints(values))
                try:
                    outcome, update = apply_edit(session, ctx, values, action, locale)
                    if outcome.plan is None:
                        action = action.model_copy(update={"confirm": outcome.needs_confirmation})
                        outcome, update = apply_edit(session, ctx, values, action, locale)
                except EditError as error:
                    raise FollowUpSetupError(f"edit {kind} {title}: {error.code}") from None
        graph.update_state(config, update, as_node="present_plan")


def hide_dishes(factory: sessionmaker[Session], titles: Sequence[str]) -> dict[str, list[dict]]:
    """Delete the library dishes `titles` for one scenario. Returns their rows (recipe, lines,
    cookware, images, ratings) so `bring_back` restores them with the same ids.

    Links the database sets to NULL on delete (another recipe's parent_recipe_id, a
    meal_plan_items row's recipe_id) are not restored; harmless today, since an evaluation
    library has no variants and saves no meal plan."""
    with factory() as session:
        ids = _recipe_ids(session, titles)
        saved: dict[str, list[dict]] = {}
        for table in _RECIPE_TABLES:
            column = table.c.id if table is Recipe.__table__ else table.c.recipe_id
            rows = session.execute(select(table).where(column.in_(ids))).mappings().all()
            saved[table.name] = [dict(row) for row in rows]
        session.execute(delete(Recipe).where(Recipe.id.in_(ids)))
        session.commit()
    return saved


def bring_back(factory: sessionmaker[Session], saved: dict[str, list[dict]]) -> None:
    """Restore what `hide_dishes` removed, ids included."""
    with factory() as session:
        for table in _RECIPE_TABLES:
            rows = saved.get(table.name) or []
            if rows:
                session.execute(insert(table), rows)
        session.commit()


# ---- judging the follow-up ----


@dataclass(frozen=True)
class TurnFacts:
    """What the judged turn did. `before` and `after` map dish id to title for the plan before
    the follow-up and the current plan after it; both are None without a follow-up."""

    turn: str = "plan"
    route: str | None = None
    choice_card: bool = False
    question: bool = False
    before: dict[int, str] | None = None
    after: dict[int, str] | None = None
    locked_before: frozenset[int] = field(default_factory=frozenset)
    # ---- plan 3b-1b ----
    # Questions the judged turn asked: ask-first and count questions (answered or not), a
    # revision that asked which dish was meant, and the empty-plan question. The choice card
    # is `choice_card`.
    questions: int = 0
    # A diner card appeared on the follow-up turn itself (not on the first message).
    follow_up_card: bool = False
    # The judged turn told the user, in code-written form, that the wish was not met
    # (`wish_unmet_said`).
    wish_unmet_said: bool = False
    # The next actions of the card the button was pressed on (with button_after).
    offered: tuple[str, ...] = ()
    # With button_after: why the button was not pressed because the turn before it stopped
    # (the runner's note), else None.
    button_blocked: str | None = None
    # The judged run's request["button"]: set when code built the request from a button.
    button: str | None = None
    # The judged run's parsed request (the request the turn planned or revised with).
    request: dict | None = None

    def gone(self) -> list[str]:
        """Titles of the plan before the follow-up that are not in the plan after it."""
        if self.before is None or self.after is None:
            return []
        return [title for i, title in self.before.items() if i not in self.after]


def plan_dishes(plan: dict | None) -> dict[int, str]:
    return {r["id"]: r["title"] for r in (plan or {}).get("recipes", [])}


def locked_of(plan: dict | None) -> frozenset[int]:
    """The dishes a revision must keep, as the revise step reads them: added by hand or
    confirmed, minus those with an open conflict."""
    if not plan:
        return frozenset()
    in_plan = set(plan_dishes(plan))
    return frozenset((locked_ids(plan) - open_conflict_ids(plan)) & in_plan)


# A follow-up on a plan is read, not routed (plan 3b-2 spec 5.1): the route each reading kind
# stands for (spec 6: change is revise, new_meal is plan). Task 10 settles the runner's keys.
ROUTE_OF_READING = {"change": "revise", "people": "revise", "new_meal": "plan", "other": "other"}


def route_of(turn_events: list[list[dict]], values: dict) -> str | None:
    """The route model's answer for the turn, or the route a follow-up reading stands for
    (`ROUTE_OF_READING`): from the route or reader call recorded in the turn's planning runs,
    else (no run: a turn that stopped before planning) from the state's pending events, else
    "other" when the turn was answered without planning. None when neither call was made
    ("plan again" reuses the parse)."""

    def found(events: list[dict]) -> str | None:
        for event in events:
            if event.get("kind") != "model_call":
                continue
            if event.get("step") == "route":
                return (event.get("response") or {}).get("intent")
            if event.get("step") == "read_follow_up":
                return ROUTE_OF_READING.get((event.get("response") or {}).get("kind"))
        return None

    for events in turn_events:
        intent = found(events or [])
        if intent is not None:
            return intent
    if turn_events:
        return None
    intent = found(values.get("model_calls") or [])
    if intent is not None:
        return intent
    return "other" if values.get("intent") == "other" else None


def asked_back(values: dict) -> bool:
    """The revise step answered "unclear" with no change: the reply asks which dish is meant
    (the graph's own rule, `asks_back`, on the state's proposal)."""
    proposal = values.get("proposal")
    return isinstance(proposal, dict) and asks_back(proposal)


def follow_up_failures(scenario: Any, facts: TurnFacts) -> list[str]:
    """What about the judged turn did not match the plan 3b-1 expect_* keys the scenario
    states. Pure."""
    failures = []
    if scenario.expect_route is not None and facts.route != scenario.expect_route:
        failures.append(f"route {facts.route}, expected {scenario.expect_route}")
    if scenario.expect_choice_card is not None and facts.choice_card != scenario.expect_choice_card:
        failures.append(
            f"choice card shown={facts.choice_card}, expected {scenario.expect_choice_card}"
        )
    if scenario.expect_question is not None and facts.question != scenario.expect_question:
        failures.append(f"question to the user={facts.question}, expected {scenario.expect_question}")
    failures += _3b1b_failures(scenario, facts)
    wants_change_keys = (
        scenario.expect_unchanged is not None
        or scenario.expect_removed
        or scenario.expect_max_changed is not None
        or scenario.expect_removed_any
    )
    if not wants_change_keys:
        return failures
    if facts.before is None or facts.after is None:
        return [*failures, "no follow-up turn to compare the plan before and after"]
    before = set(facts.before.values())
    after = set(facts.after.values())
    gone = facts.gone()
    if scenario.expect_unchanged is not None:
        unchanged = set(facts.before) == set(facts.after)
        if unchanged != scenario.expect_unchanged:
            failures.append(
                f"plan unchanged={unchanged}, expected {scenario.expect_unchanged}; "
                f"before {sorted(before)}, after {sorted(after)}"
            )
    not_there = [t for t in scenario.expect_removed if t not in before]
    if not_there:
        failures.append(f"expected removed but not in the plan before the follow-up: {not_there}")
    kept = [t for t in scenario.expect_removed if t in before and t in after]
    if kept:
        failures.append(f"still in the plan, expected removed: {kept}")
    if scenario.expect_max_changed is not None and len(gone) > scenario.expect_max_changed:
        failures.append(f"{len(gone)} dishes changed {gone}, at most {scenario.expect_max_changed}")
    if scenario.expect_removed_any and not set(scenario.expect_removed_any) & set(gone):
        failures.append(f"none of {scenario.expect_removed_any} removed; removed {gone}")
    return failures


def _3b1b_failures(scenario: Any, facts: TurnFacts) -> list[str]:
    """The plan 3b-1b keys (button_after, expect_no_question, expect_wish_unmet,
    expect_follow_up_diner_card, expect_request_kept) on `facts`. Pure."""
    failures = []
    action = getattr(scenario, "button_after", None)
    if action is not None:
        if facts.button_blocked:
            failures.append(
                f"the {action} button was not pressed: the turn before it stopped "
                f"({facts.button_blocked})"
            )
        elif action not in facts.offered:
            failures.append(
                f"the card did not offer the {action} button; offered {list(facts.offered)}"
            )
        elif facts.button != action:
            failures.append(
                f"the {action} button turn's request was not built in code "
                f"(request button {facts.button})"
            )
    wanted = scenario.expect_no_question
    if wanted is not None:
        asked = facts.questions > 0 or facts.choice_card
        if wanted and asked:
            failures.append(
                f"a question was asked ({facts.questions} questions, choice card "
                f"{facts.choice_card}), expected none"
            )
        elif not wanted and not asked:
            failures.append("no question was asked, expected one")
    if scenario.expect_wish_unmet is not None and facts.wish_unmet_said != scenario.expect_wish_unmet:
        failures.append(
            f"unmet wish said={facts.wish_unmet_said}, expected {scenario.expect_wish_unmet}"
        )
    card = scenario.expect_follow_up_diner_card
    if card is not None and facts.follow_up_card != card:
        failures.append(f"diner card on the follow-up={facts.follow_up_card}, expected {card}")
    request = facts.request or {}
    lost = [name for name in scenario.expect_request_kept if not request.get(name)]
    if lost:
        failures.append(f"request fields empty on the judged turn: {lost}")
    return failures


# An empty plan's count in words, as the old H4 text showed it for a missing answer ("方案有
# 0 道菜", "The plan has 0 dishes"). Another category at 0 ("0 道汤", "0 meat dishes") is a
# real count on a plan with dishes and does not match.
_ZERO_DISHES = re.compile(r"(?<![\d.])0\s*道菜|(?<![\w.])0\s+dish(?:es)?(?![a-z])", re.IGNORECASE)


def zero_dish_texts(texts: Iterable[str]) -> list[str]:
    """The texts that say the plan has 0 dishes (plan 3b-1b spec 5.6: never shown). Pure."""
    return [text for text in texts if text and _ZERO_DISHES.search(text)]


def _template_pattern(template: str) -> re.Pattern[str]:
    head, tail = template.split("{wish}")
    return re.compile(re.escape(head) + ".+?" + re.escape(tail), re.DOTALL)


_WISH_UNMET_NOTES = tuple(_template_pattern(t) for t in (WISH_UNMET_NOTE_ZH, WISH_UNMET_NOTE_EN))


def wish_unmet_said(card: dict | None, reply: str, turn: str) -> bool:
    """Whether the judged turn told the user, in code-written form, that the wish was not
    (fully) met. `card` is the card of the turn's last reply (None when that reply has no
    card, as the empty-plan question), `reply` its text, `turn` the judged turn's kind.
    Counts:
    - a `wish_unmet` entry in the card's unmet (a new plan whose planner said the wish was
      not met, or a revision that found nothing that fits);
    - the new-plan wish-unmet sentence (WISH_UNMET_NOTE_ZH / _EN, any wish) in the reply;
    - a revision whose card kept the plan as it was (`plan_kept`) and has at least one unmet
      entry: a failed revision (every attempt failed a check) or a nothing-fits revision.
      Its reply says the change could not be made and the card says why, so the wish
      counts as said even when no entry names the wish itself (controller ruling, Task 9).
      A kept card with no unmet entry (a which-dish question) does not count.
    Not detected: a passed revision that met the wish only in part and says so only in the
    model's own note. Pure."""
    card = card or {}
    if any(entry.get("code") == WISH_UNMET for entry in card.get("unmet") or []):
        return True
    if any(pattern.search(reply or "") for pattern in _WISH_UNMET_NOTES):
        return True
    return turn == "revision" and bool(card.get("plan_kept")) and bool(card.get("unmet"))


def fallback_count(runs_events: Iterable[Iterable[dict]]) -> int:
    """Attempts whose dishes code picked because the planner gave no answer (validation
    events marked `fallback`, plan 3b-1b spec 5.6). Pure."""
    return sum(
        1
        for events in runs_events
        for event in events or []
        if event.get("kind") == "validation" and event.get("fallback")
    )


def asked_empty(values: dict) -> bool:
    """The revision would have left no dish and asked whether to plan a new meal (spec 5.1)
    instead; read from the state's revision of the judged run."""
    revision = values.get("revision")
    return isinstance(revision, dict) and bool(revision.get("empty"))


def locked_removed(scenario: Any, facts: TurnFacts) -> list[str]:
    """Locked dishes a revision removed without the scenario expecting it (spec 5.6: target
    0). Only a revision is judged: a new plan starts from scratch by design."""
    # Revision turns only (controller ruling): a new plan drops hand edits by design (spec 5.1).
    if facts.turn != "revision" or facts.before is None or facts.after is None:
        return []
    allowed = removal_titles(scenario)
    return [
        facts.before[i]
        for i in sorted(facts.locked_before)
        if i not in facts.after and facts.before[i] not in allowed
    ]
