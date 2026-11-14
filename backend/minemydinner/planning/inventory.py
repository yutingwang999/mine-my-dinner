"""Inventory snapshot and update, and anonymous meal preferences, for the planner tools."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.constants import STOCK_LEVELS
from minemydinner.extraction.normalize import match_ingredient
from minemydinner.models import AlwaysInStock, Cookware, CookwareOwned, Ingredient, Inventory
from minemydinner.planning.context import PlanningContext, diner_codes


@dataclass(frozen=True)
class InventoryChange:
    name: str
    action: Literal["set", "remove"]
    count: int | None = None
    level: str | None = None
    grams: float | None = None
    expires_on: date | None = None


def _fits_stock_mode(stock_mode: str, data) -> bool:
    """`availability` has no quantity, `count` needs a count, and `level` needs a level
    with optional grams. Grams never stand alone. `data` is anything with `count`,
    `level`, and `grams`."""
    has = (data.count is not None, data.level is not None, data.grams is not None)
    if stock_mode == "availability":
        return has == (False, False, False)
    if stock_mode == "count":
        return has == (True, False, False)
    return has[0] is False and has[1] is True


def _values_valid(change: InventoryChange) -> bool:
    """The API's guards: a known level and no negative numbers."""
    if change.level is not None and change.level not in STOCK_LEVELS:
        return False
    return (change.count is None or change.count >= 0) and (
        change.grams is None or change.grams >= 0
    )


def _name(entry: Ingredient | Cookware) -> str:
    return entry.name_zh or entry.name_en or ""


def inventory_snapshot(
    session: Session, category: str | None = None, sort_by_expiry: bool = False
) -> dict:
    query = select(Inventory).join(Inventory.ingredient)
    if category is not None:
        query = query.where(Ingredient.category == category)
    if sort_by_expiry:
        query = query.order_by(Inventory.expires_on.asc().nulls_last(), Ingredient.name_zh)
    else:
        query = query.order_by(Ingredient.name_zh, Ingredient.name_en)
    ingredients = [
        {
            "id": row.ingredient_id,
            "name_zh": row.ingredient.name_zh,
            "name_en": row.ingredient.name_en,
            "category": row.ingredient.category,
            "stock_mode": row.ingredient.stock_mode,
            "count": row.count,
            "level": row.level,
            "grams": row.grams,
            "expires_on": row.expires_on,
        }
        for row in session.scalars(query)
    ]
    always = session.scalars(select(Ingredient).join(AlwaysInStock).order_by(Ingredient.id))
    owned = session.scalars(select(Cookware).join(CookwareOwned).order_by(Cookware.id))
    return {
        "ingredients": ingredients,
        "always_in_stock": [_name(entry) for entry in always],
        "cookware": [_name(entry) for entry in owned],
    }


def update_inventory(session: Session, changes: list[InventoryChange]) -> dict:
    """Apply stock changes. Names the dictionary does not know are returned in `unmatched`
    and never created. A "set" whose values do not fit the entry's stock mode is returned
    in `rejected`, as is a bad level or a negative number. Other changes still apply. This flushes and leaves the commit to the caller."""
    applied: list[str] = []
    unmatched: list[str] = []
    rejected: list[str] = []
    for change in changes:
        ingredient = match_ingredient(session, change.name)
        if ingredient is None:
            unmatched.append(change.name)
            continue
        row = session.get(Inventory, ingredient.id)
        if change.action == "remove":
            if row is not None:
                session.delete(row)
        else:
            if not _values_valid(change) or not _fits_stock_mode(ingredient.stock_mode, change):
                rejected.append(change.name)
                continue
            row = row or Inventory(ingredient_id=ingredient.id)
            row.count, row.level, row.grams = change.count, change.level, change.grams
            row.expires_on = change.expires_on
            session.add(row)
        applied.append(change.name)
    session.flush()
    return {"applied": applied, "unmatched": unmatched, "rejected": rejected}


def meal_preferences(session: Session, ctx: PlanningContext) -> list[str]:
    """Constraint lines for the selected diners only, under anonymous codes."""
    codes = diner_codes(ctx.constraints)
    ids = {rule.ingredient_id for rule in ctx.rules if rule.ingredient_id is not None}
    names = {
        entry.id: _name(entry)
        for entry in session.scalars(select(Ingredient).where(Ingredient.id.in_(ids)))
    }
    lines: list[str] = []
    for rule in ctx.rules:
        subject = rule.allergen or names.get(rule.ingredient_id) or rule.text or ""
        verb = {"allergy": "allergy", "dislike": "dislikes"}.get(rule.rule_type, "does not eat")
        lines.append(f"{codes[rule.diner_key]}: {verb} {subject}")
    # Unconfirmed diners have no rules to number from, so they continue after the known codes.
    for number in range(len(codes) + 1, len(codes) + ctx.constraints.unconfirmed_diners + 1):
        lines.append(f"Diner {number}: dietary limits unconfirmed")
    return lines
