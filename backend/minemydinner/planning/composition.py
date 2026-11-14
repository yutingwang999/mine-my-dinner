"""Dish categories and exact ingredient amounts, shared by validation and later checks.

One place defines how a plan's dishes are counted (design 5.5) and how exact amounts of one
ingredient add up across dishes, so validation, the shortlist, and the shopping list agree.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
SPLIT_CATEGORIES = ("meat_dishes", "vegetable_dishes")


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
    """Every category the plan must match. A category nobody asked for is required as 0;
    meat and vegetable counts appear only when named."""
    required = {
        "dishes": spec.dishes,
        "soups": spec.soups,
        "staples": spec.staples,
        "desserts": spec.desserts,
        "snacks": spec.snacks,
    }
    if spec.meat_dishes is not None:
        required["meat_dishes"] = spec.meat_dishes
    if spec.vegetable_dishes is not None:
        required["vegetable_dishes"] = spec.vegetable_dishes
    return required


def category_shortfall(category: str, required: int, present: int, minimum: bool) -> int:
    """How far one category is from its requirement. A named split that is a minimum
    only counts a shortfall."""
    if minimum and category in SPLIT_CATEGORIES:
        return max(required - present, 0)
    return abs(required - present)


def composition_gap(spec: "PlanSpec", present: dict[str, int]) -> int:
    return sum(
        category_shortfall(
            category, required, present.get(category, 0), spec.split_is_minimum
        )
        for category, required in required_counts(spec).items()
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
