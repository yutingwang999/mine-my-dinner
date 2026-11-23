"""Follow-up scenarios of the planning evaluation (plan 3b-1 spec 5.6): a fixed first plan,
3a hand edits between the turns, dishes hidden for one scenario, and the follow-up's own
checks (route, question, which dishes changed, locked dishes kept). The choice card is gone
(plan 3b-2 Task 8): its scenario keys are still read, `choice_answer` is ignored and
`expect_choice_card: true` cannot be met.

Plan 3b-2 spec 6 judges every scenario in two layers: what must never be wrong
(`must_failures`, run on every scenario of every set) and what the scenario's keys accept
(`expect_any_of` and the looser keys). Which dishes may change comes from the existing keys
and the frame the run recorded (`allowed_changes`); a question asked although a change was
reasonable is counted apart, never failed (`unnecessary_question`).

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
from minemydinner.agent.frame import RULE_BREAKS
from minemydinner.agent.graph import WISH_UNMET_NOTE_EN, WISH_UNMET_NOTE_ZH
from minemydinner.agent.labels import LabelTable, labels_scope, load_label_context
from minemydinner.agent.plan_card import build_dishes_card, card_base, meal_constraints
from minemydinner.agent.request import ParsedRequest
from minemydinner.agent.revise_step import WISH_UNMET, open_conflict_ids
from minemydinner.models import Diner, Rating, Recipe, RecipeCookware, RecipeImage, RecipeIngredient
from minemydinner.planning.composition import CATEGORY_OF_DISH_TYPE, SPLIT_CATEGORIES
from minemydinner.planning.context import load_planning_context
from minemydinner.planning.validate import PlanSpec, validate_plan

# The values the scenario keys take. "unsure" was the route model's answer that showed the
# choice card (gone since plan 3b-2 Task 8): still read so the regression sets load, never
# met. "other" is a follow-up that is not about the meal's plan.
ROUTES = ("plan", "revise", "unsure", "other")
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
    "button_before",
)
# expect_* keys an expect_any_of entry may not hold: the list itself, and the diner checks,
# which are judged once over the whole scenario.
NOT_IN_ANY_OF = ("expect_any_of", "expect_card", "expect_rules_applied", "expect_rules_not_applied")
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
    if scenario.expect_route not in (None, *ROUTES):
        raise bad(f"expect_route must be one of {list(ROUTES)}")
    for key in (
        "expect_choice_card",
        "expect_question",
        "expect_unchanged",
        "expect_wish_unmet",
        "expect_no_question",
        "expect_follow_up_diner_card",
        "expect_some_next_action",
    ):
        value = getattr(scenario, key)
        if value is not None and not isinstance(value, bool):
            raise bad(f"{key} must be true or false")
    for key in ("button_after", "button_before"):
        value = getattr(scenario, key)
        if value is not None and value not in ACTIONS:
            raise bad(f"{key} must be one of {list(ACTIONS)}")
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
    question: bool = False
    before: dict[int, str] | None = None
    after: dict[int, str] | None = None
    locked_before: frozenset[int] = field(default_factory=frozenset)
    # ---- plan 3b-1b ----
    # Questions the judged turn asked: ask-first and count questions (answered or not), a
    # revision that asked which dish was meant, and the empty-plan question.
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
    # ---- plan 3b-2: what the change path recorded, and what the user saw ----
    # The judged turn's `frame` event: the frame code built, the reading it used (on a
    # button turn, the button's), and the dishes that break the new request (id to reason).
    frame: dict | None = None
    reading: dict | None = None
    breaking: dict[int, str] = field(default_factory=dict)
    # The judged turn's `change_outcome` event.
    change: dict | None = None
    # The category counts of the plan after the follow-up (count_categories), and the dish
    # type of each dish of the plan before it.
    counts_after: dict[str, int] | None = None
    types_before: dict[int, str] = field(default_factory=dict)
    # Titles on the shown card with a diner-rule or meal-avoidance finding (H2): a revision's
    # card, or a new plan shown as passed.
    rule_breaks_shown: tuple[str, ...] = ()
    # Every reply text and card unmet text of the thread, as the user saw them.
    shown_texts: tuple[str, ...] = ()
    # How the judged turn ended when it ended asking: "question" (a question of the graph
    # left unanswered), "empty_plan", "which_dish", "unclear" (the counts contradicted each
    # other); None otherwise.
    ended_with: str | None = None
    # The judged turn's own message (the follow-up), for quotes.
    words: str = ""

    def gone(self) -> list[str]:
        """Titles of the plan before the follow-up that are not in the plan after it."""
        if self.before is None or self.after is None:
            return []
        return [title for i, title in self.before.items() if i not in self.after]

    def came(self) -> list[str]:
        """Titles of the plan after the follow-up that were not in the plan before it."""
        if self.before is None or self.after is None:
            return []
        return [title for i, title in self.after.items() if i not in self.before]

    def wish_unmet_recorded(self) -> bool:
        """The change path recorded this turn's wish as not met (spec 5.5: code says it)."""
        wish = str((self.reading or {}).get("wish") or "").strip()
        return bool(wish) and (self.change or {}).get("wish_met") is False


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
# stands for (spec 6: change is revise, new_meal is plan; people is a change in who eats on
# the plan, today's diner-card path, so revise too).
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
    """The reply asks which dish is meant: a reference that matched no dish (plan 3b-2 spec
    5.6), as the change path records it."""
    return (values.get("change") or {}).get("asked") == "which_dish"


def follow_up_failures(scenario: Any, facts: TurnFacts) -> list[str]:
    """What about the judged turn did not match the plan 3b-1 expect_* keys the scenario
    states. Pure."""
    failures = []
    if scenario.expect_route is not None and facts.route != scenario.expect_route:
        failures.append(f"route {facts.route}, expected {scenario.expect_route}")
    if scenario.expect_choice_card:
        # The card was removed (plan 3b-2 Task 8): it is never shown.
        failures.append("choice card expected, but the app no longer shows one")
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
        asked = facts.questions > 0
        if wanted and asked:
            failures.append(f"a question was asked ({facts.questions} questions), expected none")
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
    """The change would have left no dish and asked whether to plan a new meal (spec 5.1)
    instead, as the change path records it."""
    return (values.get("change") or {}).get("asked") == "empty_plan"


def _fold(text: str) -> str:
    return "".join(str(text).split()).casefold()


def quoted_ids(facts: TurnFacts) -> set[int]:
    """The plan dishes the reading removed with a quote from the turn's own message that
    the frame acted on (dropped or gave a slot), never one it refused
    (`locked_without_quote`)."""
    words = _fold(facts.words)
    frame = facts.frame or {}
    acted = set(frame.get("drops") or []) | {
        s["replaces"] for s in frame.get("slots") or [] if s.get("replaces") is not None
    }
    refused = {
        i.get("id") for i in frame.get("ignored") or [] if i.get("reason") == "locked_without_quote"
    }
    found = set()
    for item in (facts.reading or {}).get("remove") or []:
        quote = _fold(item.get("quote") or "")
        dish = item.get("id")
        if quote and quote in words and dish in acted and dish not in refused:
            found.add(dish)
    return found


def rule_broken(facts: TurnFacts) -> set[int]:
    """The plan dishes the frame found breaking a diner rule or this meal's avoidance."""
    return {i for i, reason in facts.breaking.items() if reason in RULE_BREAKS}


def locked_removed(scenario: Any, facts: TurnFacts) -> list[str]:
    """Locked dishes a revision removed without their quote, rule or avoidance (plan 3b-2
    spec 6; target 0): a locked dish may go when the reading removed it with a quote from the
    turn's own message that the frame acted on, when the frame found it breaking a diner
    rule or this meal's avoidance (never stock, cookware or the time limit, spec 5.4), or
    when the scenario expects it removed. Only a revision is judged: a new plan starts from
    scratch by design."""
    # Revision turns only (controller ruling): a new plan drops hand edits by design (spec 5.1).
    if facts.turn != "revision" or facts.before is None or facts.after is None:
        return []
    allowed = removal_titles(scenario)
    justified = rule_broken(facts) | quoted_ids(facts)
    return [
        facts.before[i]
        for i in sorted(facts.locked_before)
        if i not in facts.after and facts.before[i] not in allowed and i not in justified
    ]


# ---- the two layers (plan 3b-2 spec 6) ----

# The keys that say which dishes a follow-up may change (spec 6).
CHANGE_KEYS = (
    "expect_removed",
    "expect_removed_any",
    "expect_max_changed",
    "expect_unchanged",
    "expect_titles_include",
)
# The questions that are asked only when no change is reasonable (spec 5.6).
NEEDED_ASKS = ("empty_plan", "which_dish")
# Words of the models' input that the user must never see (spec 5.8): the plan lines'
# marks, dish ids, and "Diner N" labels.
_MARKS = re.compile(r"LOCKED|OPEN CONFLICT|KEPT CONFLICT|BREAKS THIS MEAL'S WISH")
_IDS_AND_LABELS = re.compile(
    r"(?<![A-Za-z0-9_])(?:recipe_)?id\s*[:：=#]?\s*\d+|(?<![A-Za-z0-9])diner\s*\d+",
    re.IGNORECASE,
)
# A name shorter than this never stands for one library dish ("蛋", "面").
_SHORT_NAME = 2


@dataclass(frozen=True)
class AllowedChanges:
    """Which dishes a follow-up may change (spec 6). `may_go`: titles that may leave the
    plan (None: any); `max_gone`: at most this many may leave; `must_stay`: titles that may
    not leave; `max_new`: at most this many new dishes may come in (None: not bounded)."""

    may_go: frozenset[str] | None
    max_gone: int | None
    must_stay: frozenset[str]
    max_new: int | None


def frame_ids(frame: dict | None) -> set[int]:
    """The plan dishes a frame let go: drops, replaced dishes, and choice candidates."""
    frame = frame or {}
    ids = set(frame.get("drops") or [])
    ids |= {s["replaces"] for s in frame.get("slots") or [] if s.get("replaces") is not None}
    for choice in frame.get("choices") or []:
        ids |= set(choice.get("candidates") or [])
    return ids


def frame_room(frame: dict | None) -> int | None:
    """How many new dishes a frame made room for: its slots, its named dishes placed, and up
    to N for each replace choice. None without a frame."""
    if frame is None:
        return None
    replaces = sum(
        int(c.get("up_to") or 0) for c in frame.get("choices") or [] if c.get("mode") == "replace"
    )
    return len(frame.get("slots") or []) + len(frame.get("placed") or []) + replaces


def _stated(scenario: Any, key: str) -> bool:
    if getattr(scenario, key) not in (None, [], ()):
        return True
    return any(entry.get(key) not in (None, [], ()) for entry in scenario.expect_any_of)


def allowed_changes(scenario: Any, facts: TurnFacts) -> AllowedChanges | None:
    """Which dishes the follow-up may change, from the scenario's keys and the recorded frame
    (spec 6). None when the scenario states none of CHANGE_KEYS: then only locks, counts and
    rules are checked. Named removals (expect_removed, expect_removed_any, also in
    expect_any_of entries) and the dishes the frame found breaking the new request may go;
    with no named removal, the dishes the frame let go (any dish without a frame).
    expect_max_changed bounds how many go (an expect_any_of bound counts when every entry
    states one); expect_titles_include may not go; expect_unchanged: true lets nothing go or
    come. The frame bounds how many new dishes come in. Pure."""
    if not any(_stated(scenario, key) for key in CHANGE_KEYS):
        return None
    must_stay = frozenset(scenario.expect_titles_include)
    if scenario.expect_unchanged is True:
        return AllowedChanges(frozenset(), 0, must_stay, 0)
    before = facts.before or {}
    named = set(removal_titles(scenario)) | set(scenario.expect_removed_any)
    for entry in scenario.expect_any_of:
        named.update(entry.get("expect_removed_any") or [])
    # A locked dish goes only for a rule or an avoidance (spec 5.4).
    broken = {
        before[i]
        for i, reason in facts.breaking.items()
        if i in before and (reason in RULE_BREAKS or i not in facts.locked_before)
    }
    if named:
        may_go: frozenset[str] | None = frozenset(named | broken)
    elif facts.frame is not None:
        may_go = frozenset({before[i] for i in frame_ids(facts.frame) if i in before} | broken)
    else:
        may_go = None
    max_gone = scenario.expect_max_changed
    bounds = [entry.get("expect_max_changed") for entry in scenario.expect_any_of]
    if max_gone is None and bounds and all(isinstance(b, int) for b in bounds):
        max_gone = max(bounds)
    return AllowedChanges(may_go, max_gone, must_stay, frame_room(facts.frame))


def _allowed_failures(scenario: Any, facts: TurnFacts) -> list[str]:
    # A new plan replaces every dish by design (spec 5.1); judged on changes only.
    if facts.turn == "plan" or facts.before is None or facts.after is None:
        return []
    allowed = allowed_changes(scenario, facts)
    if allowed is None:
        return []
    parts = []
    gone = facts.gone()
    # A locked dish is said once, by the locked check.
    locked = set(locked_removed(scenario, facts))
    outside = [t for t in gone if allowed.may_go is not None and t not in allowed.may_go]
    outside += [t for t in gone if t in allowed.must_stay and t not in outside]
    outside = [t for t in outside if t not in locked]
    if outside:
        parts.append(f"dishes removed that the scenario does not allow: {outside}")
    if allowed.max_gone is not None and len(gone) > allowed.max_gone:
        parts.append(f"{len(gone)} dishes removed {gone}, the scenario allows {allowed.max_gone}")
    came = facts.came()
    if allowed.max_new is not None and len(came) > allowed.max_new:
        parts.append(f"{len(came)} dishes added {came}, the change made room for {allowed.max_new}")
    # One fault, one line.
    return ["; ".join(parts)] if parts else []


def _count_failures(facts: TurnFacts) -> list[str]:
    """The counts this message stated (the reading's), met by the plan after it, unless the
    reply says what could not be done or the turn asked instead."""
    stated = {
        k: v for k, v in ((facts.reading or {}).get("counts") or {}).items() if v is not None
    }
    if not stated or facts.counts_after is None or facts.change is None:
        return []
    if facts.ended_with in NEEDED_ASKS:
        return []
    unfinished = facts.change.get("unfinished") or []
    if any(_touches(item, stated, facts.types_before) for item in unfinished):
        # The reply says a part that bears on a stated count could not be done.
        return []
    wrong = {
        k: (facts.counts_after.get(k, 0), v)
        for k, v in stated.items()
        if facts.counts_after.get(k, 0) != v
    }
    return [f"stated counts not met (got, stated): {wrong}"] if wrong else []


# The counts an added dish of no known type may fall in: the dish total and its sides.
_ADD_COUNTS = ("dishes", *SPLIT_CATEGORIES)


def _touches(item: dict, stated: dict, types: dict[int, str]) -> bool:
    """An unfinished part bears on a stated count (spec 5.5 lowers or keeps a count for it,
    and the reply says so):
    - an add for a count (`for_count`);
    - an unfilled slot whose dish type falls in a stated category;
    - a `removed` dish (it broke a rule and nothing replaced it): its own type's category;
    - a `missing` named dish: the type of the dish it was to replace, or for an add the
      dish total and the sides.
    A meat or vegetable dish also bears on the dish total. A type that cannot be found bears
    on every stated count."""
    if item.get("for_count"):
        return True
    kind = item.get("kind")
    if kind == "removed":
        dish_type = types.get(item.get("recipe_id"))
    elif kind == "missing":
        replaces = item.get("replaces")
        if replaces is None:
            return any(name in stated for name in _ADD_COUNTS)
        dish_type = types.get(replaces)
    else:
        dish_type = item.get("dish_type") or ""
        if not dish_type:
            return False
    category = CATEGORY_OF_DISH_TYPE.get(dish_type or "")
    if category is None:
        return True
    return category in stated or (category in SPLIT_CATEGORIES and "dishes" in stated)


def _rule_failures(facts: TurnFacts) -> list[str]:
    """No dish that breaks a diner rule or this meal's avoidance stays (spec 5.5: with no
    replacement it is removed), unless the change would leave no dish and asked."""
    kept = [
        (facts.before or {}).get(i, str(i))
        for i, reason in facts.breaking.items()
        if reason in RULE_BREAKS and i in (facts.after or {}) and facts.ended_with != "empty_plan"
    ]
    kept += [t for t in facts.rule_breaks_shown if t not in kept]
    return [f"dishes that break a diner rule or this meal's avoidance: {kept}"] if kept else []


def library_dish(name: str, library_titles: Collection[str]) -> str | None:
    """The library dish a name stands for: the title it equals (case and spaces ignored),
    else the one title that contains it ("小白菜" in "清炒小白菜"; a longer name that holds
    a title, as "番茄炒蛋盖饭", is another dish). None for a name shorter than two
    characters or one that fits no title or several. Pure."""
    key = _fold(name)
    if len(key) < _SHORT_NAME:
        return None
    titles = {_fold(t): t for t in library_titles}
    if key in titles:
        return titles[key]
    found = [t for folded, t in titles.items() if key in folded]
    return found[0] if len(found) == 1 else None


def said_not_in_library(facts: TurnFacts, library_titles: Collection[str]) -> list[str]:
    """The names the change said are not in the library that stand for a library dish."""
    named = [
        item.get("name") or ""
        for item in (facts.change or {}).get("unfinished") or []
        if item.get("kind") == "missing" and item.get("reason") == "not_in_library"
    ]
    return [n for n in named if library_dish(n, library_titles) is not None]


def internal_words(texts: Iterable[str]) -> list[str]:
    """The internal words in the texts the user sees, in order, each once: the plan lines'
    marks, dish ids and "Diner N" labels. Pure."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        matches = [*_MARKS.finditer(text), *_IDS_AND_LABELS.finditer(text)]
        for match in sorted(matches, key=lambda m: m.start()):
            if match.group(0) not in found:
                found.append(match.group(0))
    return found


def must_failures(
    scenario: Any, facts: TurnFacts, library_titles: Collection[str] = ()
) -> list[str]:
    """What must never be wrong (plan 3b-2 spec 6), on every scenario of every set; a check
    that needs what only a change records applies only where it was recorded. Each failure
    starts with "must: ". Pure.

    - no locked dish removed without its quote, rule or avoidance (`locked_removed`);
    - no dish removed or added that the scenario does not allow (`allowed_changes`);
    - the counts the message stated are met;
    - diner and one-meal rules are met;
    - no "not in the library" for a library dish;
    - no "0 dishes";
    - an unmet wish is said;
    - no internal words shown (marks, ids, "Diner N")."""
    failures = []
    locked = locked_removed(scenario, facts)
    if locked:
        failures.append(f"locked dishes removed without their quote, rule or avoidance: {locked}")
    failures += _allowed_failures(scenario, facts)
    failures += _count_failures(facts)
    failures += _rule_failures(facts)
    missing = said_not_in_library(facts, library_titles)
    if missing:
        failures.append(f"said not in the library, but a library dish: {missing}")
    zero = zero_dish_texts(facts.shown_texts)
    if zero:
        failures.append(f"a reply or unmet text says the plan has 0 dishes: {zero}")
    if facts.wish_unmet_recorded() and not facts.wish_unmet_said:
        failures.append("the wish was not met and the reply does not say so")
    words = internal_words(facts.shown_texts)
    if words:
        failures.append(f"internal words shown: {words}")
    return [f"must: {f}" for f in failures]


def _stays(keys: dict) -> bool:
    """The keys accept no change: a question is expected, the message is not about the plan
    (route other), or the plan stays and no new version is made (dishes kept with new
    conditions is a change)."""
    return (
        keys.get("expect_route") == "other"
        or keys.get("expect_question") is True
        or keys.get("expect_no_question") is False
        or (keys.get("expect_unchanged") is True and keys.get("expect_plan") is False)
    )


def change_allowed(scenario: Any) -> bool:
    """A follow-up scenario where a reasonable change exists: its keys (or one expect_any_of
    entry) accept a change. Pure."""
    if not scenario.follow_up:
        return False
    keys = {
        "expect_route": scenario.expect_route,
        "expect_question": scenario.expect_question,
        "expect_no_question": scenario.expect_no_question,
        "expect_unchanged": scenario.expect_unchanged,
        "expect_plan": scenario.expect_plan,
    }
    if _stays(keys):
        return False
    entries = scenario.expect_any_of
    return not entries or not all(_stays({**keys, **entry}) for entry in entries)


def unnecessary_question(scenario: Any, facts: TurnFacts) -> bool:
    """The follow-up ended with a question other than the empty-plan and the which-dish
    question while a change was reasonable (spec 6: counted apart, never a failure). Pure."""
    if facts.before is None:
        # The follow-up was never sent (the first message stopped first).
        return False
    asked = facts.ended_with is not None and facts.ended_with not in NEEDED_ASKS
    return asked and change_allowed(scenario)


def change_path_counts(runs_events: Iterable[Iterable[dict]]) -> dict[str, int]:
    """Reader calls, picker calls, picker attempts and frame `ignored` entries recorded in the
    runs' events. Pure."""
    counts = {"reader_calls": 0, "picker_calls": 0, "picker_attempts": 0, "frame_ignored": 0}
    for events in runs_events:
        for event in events or []:
            kind = event.get("kind")
            if kind == "model_call" and event.get("step") == "read_follow_up":
                counts["reader_calls"] += 1
            elif kind == "model_call" and event.get("step") == "pick":
                counts["picker_calls"] += 1
            elif kind == "pick_attempt":
                counts["picker_attempts"] += 1
            elif kind == "frame":
                counts["frame_ignored"] += len((event.get("frame") or {}).get("ignored") or [])
    return counts


def change_events(runs_events: Iterable[Iterable[dict]]) -> tuple[dict | None, dict | None]:
    """The last `frame` and `change_outcome` events of the runs."""
    frame = outcome = None
    for events in runs_events:
        for event in events or []:
            if event.get("kind") == "frame":
                frame = event
            elif event.get("kind") == "change_outcome":
                outcome = event
    return frame, outcome
