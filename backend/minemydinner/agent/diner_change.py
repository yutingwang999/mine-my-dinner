"""When who eats changes on an existing plan (plan 3b-2 spec 5.3 and 5.4): how the dish counts
move with the headcount, whether the message's own counts turn that off, when "more than half
must change" means making the plan again, and the constraints of a confirmed diner set. Pure:
no model, no database."""

from dataclasses import asdict

from minemydinner.agent.diners import SettledDiners, constraints_from
from minemydinner.agent.follow_up import FollowUpReading
from minemydinner.agent.frame import RULE_BREAKS
from minemydinner.agent.request import default_composition
from minemydinner.planning.composition import CATEGORY_OF_DISH_TYPE


def moved_counts(old_headcount: int, new_headcount: int, present: dict[str, int]) -> dict[str, int]:
    """The counts the new headcount moves: the plan's dish (or soup) count shifts by the change
    in the default composition, so a stated count stays stated. `present` is the plan's
    `count_categories`. A count whose default does not change is not in the result. The dishes
    never go below one, or below the plan's own count when that is zero, so a plan with no
    meat or vegetable dish never gains one when people leave."""
    d0, s0 = default_composition(old_headcount)
    d1, s1 = default_composition(new_headcount)
    moved: dict[str, int] = {}
    if d1 != d0:
        moved["dishes"] = max(min(1, present["dishes"]), present["dishes"] + d1 - d0)
    if s1 != s0:
        moved["soups"] = max(0, present["soups"] + s1 - s0)
    return moved


def message_sets_counts(reading: FollowUpReading) -> bool:
    """True when the message states its own counts: an add, or any count field. A removal
    does not."""
    return bool(reading.add) or any(v is not None for v in reading.counts.model_dump().values())


def must_change(
    breaking: dict[int, str],
    present: dict[str, int],
    moved: dict[str, int],
    dish_types: dict[int, str],
) -> int:
    """How many dishes must change. A count drop takes a rule-breaking dish first (it is a
    replace slot already), so that dish changes once. Per category the count is therefore the
    larger of the rule breakers and the dishes dropped: for the dishes (meat and vegetable
    dishes together) and for the soups. A rule breaker of any other type adds on top.
    `dish_types` maps a plan dish id to its dish type."""
    broken = {"dishes": 0, "soups": 0}
    other = 0
    for dish_id, reason in breaking.items():
        if reason not in RULE_BREAKS:
            continue
        category = CATEGORY_OF_DISH_TYPE.get(dish_types.get(dish_id, ""))
        if category in ("meat_dishes", "vegetable_dishes"):
            broken["dishes"] += 1
        elif category == "soups":
            broken["soups"] += 1
        else:
            other += 1
    total = other
    for key in ("dishes", "soups"):
        dropped = max(0, present[key] - moved.get(key, present[key]))
        total += max(broken[key], dropped)
    return total


def remakes(plan_size: int, must: int) -> bool:
    """More than half of the plan must change: make it again."""
    return must * 2 > plan_size


def people_constraints(settled: dict | None, old: dict) -> dict:
    """The constraints the change path uses: from the confirmed diner set when there is one,
    otherwise `old` unchanged (a skipped card). The meal avoidance is the caller's."""
    if settled is None:
        return old
    constraints = constraints_from(SettledDiners.from_state(settled))
    return {
        "diner_ids": list(constraints.diner_ids),
        "guest_rules": [asdict(rule) for rule in constraints.guest_rules],
        "unconfirmed_diners": constraints.unconfirmed_diners,
        "headcount": settled["headcount"],
    }
