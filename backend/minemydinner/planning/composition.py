"""Dish categories and exact ingredient amounts, shared by validation and later checks.

One place defines how a plan's dishes are counted (design 5.5) and how exact amounts of one
ingredient add up across dishes, so validation, the shortlist, and the shopping list agree.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from minemydinner.models import Recipe
from minemydinner.planning.context import PlanningContext

if TYPE_CHECKING:
    from minemydinner.planning.validate import PlanSpec

# "other" (drinks, sauces) maps to nothing and is never counted.
CATEGORY_OF_DISH_TYPE = {
    "meat dish": "meat_dishes",
    "vegetable dish": "vegetable_dishes",
    "soup": "soups",
    "staple food": "staples",
    "dessert": "desserts",
    "snack": "snacks",
}
DISH_TYPE_OF_CATEGORY = {category: dish_type for dish_type, category in CATEGORY_OF_DISH_TYPE.items()}
# The dish types that count toward the dish total (the meat and vegetable dishes).
COUNTED_DISH_TYPES = tuple(DISH_TYPE_OF_CATEGORY[category] for category in ("meat_dishes", "vegetable_dishes"))
SPLIT_CATEGORIES = ("meat_dishes", "vegetable_dishes")
# How a named meat or vegetable count binds (design 5.11).
Bound = Literal["exact", "at_least", "at_most"]


def count_categories(dish_types: Iterable[str]) -> dict[str, int]:
    counts = {category: 0 for category in CATEGORY_OF_DISH_TYPE.values()}
    for dish_type in dish_types:
        category = CATEGORY_OF_DISH_TYPE.get(dish_type)
        if category is not None:
            counts[category] += 1
    return {
        "dishes": counts["meat_dishes"] + counts["vegetable_dishes"],
        "soups": counts["soups"],
        "meat_dishes": counts["meat_dishes"],
        "vegetable_dishes": counts["vegetable_dishes"],
        "staples": counts["staples"],
        "desserts": counts["desserts"],
        "snacks": counts["snacks"],
    }


def required_counts(spec: "PlanSpec") -> dict[str, int]:
    """The categories the plan must match exactly. A category nobody asked for is required
    as 0. The meat and vegetable sides are ranges (side_ranges), not exact counts."""
    return {
        "dishes": spec.dishes,
        "soups": spec.soups,
        "staples": spec.staples,
        "desserts": spec.desserts,
        "snacks": spec.snacks,
    }


def ranges_for(
    total: int,
    meat: int | None,
    meat_bound: Bound | None,
    vegetable: int | None,
    vegetable_bound: Bound | None,
) -> dict[str, tuple[int, int]]:
    """The range [low, high] of each side for a dish total and the named sides (design 5.11).

    A named side gives exact n -> [n, n], at least n -> [n, T], at most n -> [0, n]; an
    unnamed side is [0, T]. A count with no bound is exact. Both sides named exactly with a
    sum below the total are minimums. Then each side is tightened by the other, because
    meat plus vegetable dishes equal T. A range may come out empty (low above high): the
    request contradicts its total."""
    named = {"meat_dishes": (meat, meat_bound), "vegetable_dishes": (vegetable, vegetable_bound)}
    bounds = {side: (bound or "exact") if n is not None else None for side, (n, bound) in named.items()}
    if (
        meat is not None
        and vegetable is not None
        and bounds["meat_dishes"] == bounds["vegetable_dishes"] == "exact"
        and meat + vegetable < total
    ):
        bounds = {side: "at_least" for side in bounds}
    raw: dict[str, tuple[int, int]] = {}
    for side, (n, _) in named.items():
        bound = bounds[side]
        if n is None or bound is None:
            raw[side] = (0, total)
        elif bound == "exact":
            raw[side] = (n, n)
        elif bound == "at_least":
            raw[side] = (n, total)
        else:
            raw[side] = (0, n)
    (m_low, m_high), (v_low, v_high) = raw["meat_dishes"], raw["vegetable_dishes"]
    return {
        "meat_dishes": (max(m_low, total - v_high), min(m_high, total - v_low)),
        "vegetable_dishes": (max(v_low, total - m_high), min(v_high, total - m_low)),
    }


def side_ranges(spec: "PlanSpec") -> dict[str, tuple[int, int]]:
    """The meat and vegetable ranges of a spec. Validation, feasibility, the shortlist, and
    every text read the sides from here; nothing else computes them."""
    return ranges_for(
        spec.dishes, spec.meat_dishes, spec.meat_bound, spec.vegetable_dishes, spec.vegetable_bound
    )


def outside(present: int, low: int, high: int) -> int:
    """How far a count is outside [low, high]; 0 inside."""
    return max(low - present, present - high, 0)


def composition_gap(spec: "PlanSpec", present: dict[str, int]) -> int:
    """How far a plan's counts are from the spec: the distance from each exact count plus
    the distance outside each side's range."""
    exact = sum(
        abs(required - present.get(category, 0))
        for category, required in required_counts(spec).items()
    )
    return exact + sum(
        outside(present.get(side, 0), low, high) for side, (low, high) in side_ranges(spec).items()
    )


@dataclass
class ExactNeed:
    """The summed exact amount of one ingredient across dishes, against stock."""

    ingredient_id: int
    name: str
    unit: str
    needed: float
    have: float
    # One entry per recipe: {"recipe_id", "amount"}.
    parts: list[dict] = field(default_factory=list)


def sum_exact_amounts(ctx: PlanningContext, recipes: Sequence[Recipe]) -> dict[int, ExactNeed]:
    """Per stocked ingredient, the sum of its exact lines (grams against grams, pieces
    against pieces). Optional, vague, level, and unit-mismatched lines are skipped."""
    needs: dict[int, ExactNeed] = {}
    for recipe in recipes:
        for line in recipe.ingredients:
            item = ctx.stock.get(line.ingredient_id) if line.ingredient_id else None
            if item is None or line.optional or line.amount is None:
                continue
            if line.amount_type == "exact" and line.unit == "g":
                have, unit = item.grams, "g"
            elif line.amount_type == "count" and line.unit == "piece":
                have, unit = item.count, "piece"
            else:
                continue
            if have is None:
                continue
            need = needs.get(line.ingredient_id)
            if need is None:
                need = needs[line.ingredient_id] = ExactNeed(
                    line.ingredient_id, line.name, unit, 0, have
                )
            elif need.unit != unit:
                continue
            need.needed += line.amount
            part = next((p for p in need.parts if p["recipe_id"] == recipe.id), None)
            if part is None:
                need.parts.append({"recipe_id": recipe.id, "amount": line.amount})
            else:
                part["amount"] += line.amount
    return needs
