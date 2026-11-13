from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.models import Recipe, RecipeCookware, RecipeIngredient
from minemydinner.planning.context import PlanningContext


@dataclass(frozen=True)
class MissingLine:
    recipe_id: int
    name: str
    ingredient_id: int | None
    reason: Literal["not_in_stock", "level_none", "unrecognized"]
    choice_group: int | None


def _held(ctx: PlanningContext, ingredient_id: int, role: str) -> bool:
    """True when this exact ingredient is always in stock or has a usable stock row."""
    if ingredient_id in ctx.always_in_stock:
        return True
    item = ctx.stock.get(ingredient_id)
    if item is None or item.level == "none":
        return False
    # A seasoning only needs to be on the shelf, whatever its count says.
    return role == "seasoning" or item.count != 0


def is_available(ctx: PlanningContext, ingredient_id: int, role: str) -> bool:
    """A need is met by the ingredient itself or, for a parent, by one of its children."""
    if _held(ctx, ingredient_id, role):
        return True
    return any(
        parent_id == ingredient_id and _held(ctx, child_id, role)
        for child_id, parent_id in ctx.parents.items()
    )


def _reason(ctx: PlanningContext, ingredient_id: int) -> Literal["not_in_stock", "level_none"]:
    item = ctx.stock.get(ingredient_id)
    return "level_none" if item is not None and item.level == "none" else "not_in_stock"


def compute_missing(
    session: Session, ctx: PlanningContext, recipe_ids: list[int]
) -> dict[int, list[MissingLine]]:
    result: dict[int, list[MissingLine]] = {recipe_id: [] for recipe_id in recipe_ids}
    rows = session.scalars(
        select(RecipeIngredient)
        .where(RecipeIngredient.recipe_id.in_(recipe_ids), RecipeIngredient.optional.is_(False))
        .order_by(RecipeIngredient.recipe_id, RecipeIngredient.id)
    )
    candidates: dict[int, list[tuple[MissingLine, bool]]] = {}
    for row in rows:
        if row.ingredient_id is None:
            line = MissingLine(row.recipe_id, row.name, None, "unrecognized", row.choice_group)
            ok = False
        else:
            ok = is_available(ctx, row.ingredient_id, row.role)
            line = MissingLine(
                row.recipe_id,
                row.name,
                row.ingredient_id,
                _reason(ctx, row.ingredient_id),
                row.choice_group,
            )
        candidates.setdefault(row.recipe_id, []).append((line, ok))

    for recipe_id, entries in candidates.items():
        satisfied_groups = {line.choice_group for line, ok in entries if ok and line.choice_group is not None}
        result[recipe_id] = [
            line
            for line, ok in entries
            if not ok and line.choice_group not in satisfied_groups
        ]
    return result


def missing_cookware(
    session: Session, ctx: PlanningContext, recipe_ids: list[int]
) -> dict[int, list[tuple[int, str]]]:
    result: dict[int, list[tuple[int, str]]] = {recipe_id: [] for recipe_id in recipe_ids}
    langs = dict(
        session.execute(select(Recipe.id, Recipe.lang).where(Recipe.id.in_(recipe_ids))).all()
    )
    rows = session.scalars(
        select(RecipeCookware)
        .where(RecipeCookware.recipe_id.in_(recipe_ids))
        .order_by(RecipeCookware.recipe_id, RecipeCookware.cookware_id)
    )
    for row in rows:
        if row.cookware_id in ctx.cookware_owned:
            continue
        cookware = row.cookware
        names = (cookware.name_zh, cookware.name_en)
        if langs.get(row.recipe_id) == "en":
            names = names[::-1]
        name = names[0] or names[1] or ""
        result[row.recipe_id].append((row.cookware_id, name))
    return result
