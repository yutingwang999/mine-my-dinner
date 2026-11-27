"""The plan card's next-action buttons, applied in code (plan 3b-1b spec 5.5). A button takes
the request of the turn whose card offered it and changes one field; no route and no parse
call is made."""

from minemydinner.agent.request import (
    ACTION_MESSAGES,
    COUNT_FIELDS,
    ParsedRequest,
    default_composition,
    dish_total,
    request_problem,
)
from minemydinner.planning.composition import SPLIT_CATEGORIES, count_categories

ACTIONS = (
    "switch_to_shopping",
    "fewer_dishes",
    "drop_time_limit",
    "relax_split",
    "relax_named",
)
# The actions that change a count: the revision takes the counts the changed request states.
COUNT_ACTIONS = ("fewer_dishes", "relax_split")
_BOUNDS = {"meat_dishes": "meat_bound", "vegetable_dishes": "vegetable_bound"}
_OTHER_COUNTS = ("soups", "staples", "desserts", "snacks")


def action_of(text: str) -> str | None:
    """The action whose fixed message (in any locale) `text` is, or None."""
    text = text.strip()
    for texts in ACTION_MESSAGES.values():
        for action, message in texts.items():
            if text == message:
                return action
    return None


def _total(request: dict, spec: dict | None) -> int | None:
    """The dish total the request asks for: its own, else the spec's (the card's), else the
    one its named sides give. None when nothing gives one."""
    if request.get("dishes") is not None:
        return request["dishes"]
    if spec is not None and spec.get("dishes") is not None:
        return spec["dishes"]
    parsed = ParsedRequest.model_validate(request)
    return dish_total(parsed, parsed.headcount or 1) or None


def _without_range(request: dict, names: tuple[str, ...]) -> list:
    """`range_fields` without the fields the button sets: they are no longer assumed."""
    return [n for n in request.get("range_fields") or [] if n not in names]


def _stated_counts(request: dict) -> bool:
    return any(request.get(name) is not None for name in COUNT_FIELDS)


def _fewer_dishes(request: dict, spec: dict | None) -> dict:
    changed = dict(request)
    if not _stated_counts(request) and spec is not None:
        # Every count was a default: the card's counts stand, so a default soup stays.
        for name in _OTHER_COUNTS:
            changed[name] = spec.get(name)
    total = _total(request, spec)
    if total is None:
        total = default_composition(request.get("headcount") or 1)[0]
    if total > 1:
        total -= 1
    changed["dishes"] = total
    for side in SPLIT_CATEGORIES:
        count = changed.get(side)
        if count is not None and count > total:
            changed[side] = total
    if request_problem(ParsedRequest.model_validate(changed)):
        # Two named sides above the new total would contradict it: each becomes at most.
        for side, bound in _BOUNDS.items():
            if changed.get(side) is not None:
                changed[bound] = "at_most"
    changed["range_fields"] = _without_range(request, ("dishes", *SPLIT_CATEGORIES))
    return changed


def relax_split(request: dict, spec: dict | None) -> dict:
    """The request with its meat and vegetable split cleared and its total kept: the
    request's own, else the spec's (`_total`). Shared by the relax_split button and a
    reading's `any_mix` (`request_after`)."""
    changed = dict(request)
    total = _total(request, spec)
    if total is not None:
        changed["dishes"] = total
    for side, bound in _BOUNDS.items():
        changed[side] = None
        changed[bound] = None
    changed["range_fields"] = _without_range(request, SPLIT_CATEGORIES)
    return changed


def apply_action(request: dict, action: str, spec: dict | None = None) -> dict:
    """The request with one button's change (pure). `spec` is the spec of the card that
    offered the button; a total the request does not state comes from it.

    Every changed request has `counts_stated_now` set, so a revision keeps the counts it
    states: true for a count change (fewer_dishes lowers the total, relax_split keeps it
    without sides), and for any other action what the turn itself stated (switch_to_shopping
    keeps the counts)."""
    if action == "switch_to_shopping":
        changed = {**request, "mode": "shopping"}
    elif action == "fewer_dishes":
        changed = _fewer_dishes(request, spec)
    elif action == "drop_time_limit":
        changed = {**request, "time_limit_min": None}
    elif action == "relax_split":
        changed = relax_split(request, spec)
    elif action == "relax_named":
        named = [*(request.get("must_use") or []), *(request.get("all_use") or [])]
        prefer = list(dict.fromkeys([*(request.get("prefer_use") or []), *named]))
        changed = {**request, "must_use": [], "all_use": [], "prefer_use": prefer}
    else:
        raise ValueError(f"Unknown action {action}")
    changed["counts_stated_now"] = (
        True if action in COUNT_ACTIONS else bool(request.get("counts_stated_now"))
    )
    return changed


def on_plan_counts(request: dict, plan: dict) -> dict:
    """The request with its counts rebased on the plan the user sees (3b-1 spec 5.2: the
    current plan is a revision's baseline), as `revision_spec` computes them: the plan's own
    category counts, the sides exact. Used for a button that revises again when the card's
    turn stated no count, so "one fewer" is one fewer than the dishes shown and a dish removed
    by hand stays removed. A plan with no dishes keeps the request's counts."""
    if not plan.get("recipes"):
        return dict(request)
    present = count_categories(r["dish_type"] for r in plan["recipes"])
    return {
        **request,
        **{name: present[name] for name in COUNT_FIELDS},
        "meat_bound": "exact",
        "vegetable_bound": "exact",
        "range_fields": [],
    }


def spec_changes(request: dict, action: str) -> dict:
    """The revision spec fields a button must set itself: a revision takes a field only when
    the request states it, and a cleared time limit or emptied named list states nothing."""
    if action == "drop_time_limit":
        return {"time_limit_min": None}
    if action == "relax_named":
        return {"must_use": [], "all_use": [], "prefer_use": list(request.get("prefer_use") or [])}
    return {}
