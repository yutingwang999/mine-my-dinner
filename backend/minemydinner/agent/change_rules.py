"""Pure rules of a change to the current plan (plan 3b-1 spec 5.2, 5.3; plan 3b-2 spec 5.4): no
model and no database. The wish's change-size limit, the spec a change starts from, and the
conditions a follow-up leaves unstated. (The match of a quote against a dish's title or
ingredients left in plan 3b-2 Task 10b: code checks only that a quote is in the message.) The plan 3b-1 revise
step's change list (`RevisionProposal`) and its R1-R9 checks are gone (plan 3b-2): the frame
builds the change in code."""

import math
from dataclasses import dataclass

from minemydinner.agent.request import COUNT_FIELDS
from minemydinner.planning.composition import SPLIT_CATEGORIES, count_categories
from minemydinner.planning.validate import PlanSpec


@dataclass(frozen=True)
class DishFacts:
    """What the change path knows of a dish, built by the caller from the database."""

    title: str
    ingredient_names: list[str]
    dish_type: str = ""


def max_general_changes(unlocked: int) -> int:
    """How many unlocked dishes a wish may replace: half, rounded up, at least one."""
    if unlocked <= 0:
        return 0
    return max(1, math.ceil(unlocked / 2))


_REPLACED_FIELDS = ("mode", "time_limit_min", "must_use", "prefer_use", "all_use")


def _stated(request: dict, previous: dict | None, name: str) -> bool:
    """The request states the field and it differs from the previous request. A null or an
    empty list is not a statement, so it never clears a condition."""
    value = request.get(name)
    if value is None or value == []:
        return False
    return value != (previous or {}).get(name)


def _changed_counts(request: dict, previous: dict | None) -> set[str]:
    """The count fields the newest message sets. From parse_v6 the parse says whether the
    message states a count (`counts_stated_now`): if it does, every non-null total and
    category count is taken, even one equal to the previous request (a hand-trimmed plan of
    2, asked as 3, with "再加一道" is 3), but a meat or vegetable side only when it differs
    from the previous request, so a split carried over from an earlier message never becomes a
    stale exact side; if not, none is. A request parsed before the field has it null: then a
    count is taken only when it differs from the previous request."""
    stated_now = request.get("counts_stated_now")
    if stated_now is None:
        return {n for n in COUNT_FIELDS if _stated(request, previous, n)}
    if not stated_now:
        return set()
    return {
        n
        for n in COUNT_FIELDS
        if (_stated(request, previous, n) if n in SPLIT_CATEGORIES else request.get(n) is not None)
    }


def revision_spec(plan: dict, previous_request: dict | None, request: dict) -> PlanSpec:
    """The spec of a revision (spec 5.2, 5.3). The counts are the plan's own, as exact counts,
    so a hand-grown plan keeps its size; a count the message changes replaces its field
    (`_changed_counts`). A plan with no dishes keeps the spec's counts. Other fields change only
    when the request states them anew."""
    spec = PlanSpec.model_validate(plan["spec"])
    updates: dict = {}
    if plan["recipes"]:
        present = count_categories(r["dish_type"] for r in plan["recipes"])
        updates.update(
            dishes=present["dishes"],
            soups=present["soups"],
            meat_dishes=present["meat_dishes"],
            vegetable_dishes=present["vegetable_dishes"],
            meat_bound="exact",
            vegetable_bound="exact",
            staples=present["staples"],
            desserts=present["desserts"],
            snacks=present["snacks"],
        )
    changed = _changed_counts(request, previous_request)
    for name in ("soups", "staples", "desserts", "snacks"):
        if name in changed:
            updates[name] = request[name]
    if "dishes" in changed:
        updates["dishes"] = request["dishes"]
    # A new total makes the old split meaningless, and a changed side leaves the other side
    # free: only a side the request itself changes carries a count. The total stays the plan's
    # unless the request changes it.
    if changed & {"dishes", *SPLIT_CATEGORIES}:
        for name in SPLIT_CATEGORIES:
            bound_name = name.split("_")[0] + "_bound"
            if name in changed:
                updates[name] = request[name]
                updates[bound_name] = request.get(bound_name) or "exact"
            else:
                updates[name] = None
                updates[bound_name] = None
    for name in _REPLACED_FIELDS:
        if _stated(request, previous_request, name):
            updates[name] = request[name]
    return PlanSpec.model_validate({**spec.model_dump(), **updates})


# The conditions of a request a revise turn's parse may leave unstated: a null or an empty list
# is no statement, so the plan's request fills it (development run 3 of plan 3b-1b).
_CARRIED_CONDITIONS = (
    "mode",
    "time_limit_min",
    "query",
    "must_use",
    "prefer_use",
    "all_use",
    "exclude_recent_days",
    "meal_avoid",
)
# The count fields a request states as one group: a new total never keeps an old split.
COUNT_GROUP = (*COUNT_FIELDS, "meat_bound", "vegetable_bound", "range_fields")


def unset(value) -> bool:
    """A null or an empty list: no statement."""
    return value is None or value == []


def carry_unstated(parsed: dict, plan_request: dict | None) -> dict:
    """A revise turn's parse with its unstated fields filled from the request the current plan
    was built from, so a null never reads as a value: not as "only me" in the diner-card check
    and not as a default in a new plan the turn falls back to (a change in who eats).

    - The conditions (`_CARRIED_CONDITIONS`) are filled one by one.
    - The counts are filled as a group, only when the parse states none and the message
      states no count (`counts_stated_now` not true), so an old split never joins a new total.
    - Who eats (`headcount`, `others_mentioned`) is filled only when the parse says nothing
      about people: no headcount, nobody else mentioned, no guest and no friend rule. A stated
      headcount (also 1) stays, so "就我自己吃" still shows the card.
    - Guests, friend rules and the user's own rules are never filled: the settled diner set
      holds them, and a stated one is a change in who eats.

    The revision itself compares with the same request (`revision_spec`), where a value equal
    to it is no change, so filling changes nothing there."""
    if not plan_request:
        return parsed
    filled = dict(parsed)
    for name in _CARRIED_CONDITIONS:
        if unset(filled.get(name)) and not unset(plan_request.get(name)):
            filled[name] = plan_request[name]
    if filled.get("counts_stated_now") is not True and all(
        unset(filled.get(name)) for name in COUNT_GROUP
    ):
        for name in COUNT_GROUP:
            if not unset(plan_request.get(name)):
                filled[name] = plan_request[name]
    mentions_people = (
        filled.get("headcount") is not None
        or filled.get("others_mentioned")
        or filled.get("guests")
        or filled.get("friend_rules")
    )
    if not mentions_people:
        filled["headcount"] = plan_request.get("headcount")
        filled["others_mentioned"] = bool(plan_request.get("others_mentioned"))
    return filled
