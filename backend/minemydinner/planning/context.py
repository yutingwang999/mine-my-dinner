from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.models import (
    AlwaysInStock,
    CookwareOwned,
    DietaryRule,
    Ingredient,
    Inventory,
)


@dataclass(frozen=True)
class RuleRef:
    """One dietary rule. `diner_key` is "diner:<id>" or "guest:<n>" for a temporary guest."""

    diner_key: str
    rule_type: str
    allergen: str | None
    ingredient_id: int | None
    text: str | None


@dataclass(frozen=True)
class MealConstraints:
    diner_ids: tuple[int, ...]
    guest_rules: tuple[RuleRef, ...] = ()
    unconfirmed_diners: int = 0


@dataclass(frozen=True)
class StockItem:
    ingredient_id: int
    count: int | None
    level: str | None
    grams: float | None
    expires_on: date | None


@dataclass(frozen=True)
class PlanningContext:
    constraints: MealConstraints
    stock: dict[int, StockItem]
    always_in_stock: frozenset[int]
    cookware_owned: frozenset[int]
    rules: tuple[RuleRef, ...]
    parents: dict[int, int | None]
    stock_modes: dict[int, str]


def load_planning_context(session: Session, constraints: MealConstraints) -> PlanningContext:
    """Read everything the hard checks need, one query per table."""
    stock = {
        row.ingredient_id: StockItem(
            row.ingredient_id, row.count, row.level, row.grams, row.expires_on
        )
        for row in session.scalars(select(Inventory))
    }
    always = frozenset(session.scalars(select(AlwaysInStock.ingredient_id)))
    cookware = frozenset(session.scalars(select(CookwareOwned.cookware_id)))
    diner_rules = ()
    if constraints.diner_ids:
        diner_rules = tuple(
            RuleRef(
                f"diner:{row.diner_id}", row.rule_type, row.allergen, row.ingredient_id, row.text
            )
            for row in session.scalars(
                select(DietaryRule)
                .where(DietaryRule.diner_id.in_(constraints.diner_ids))
                .order_by(DietaryRule.id)
            )
        )
    # The whole dictionary covers every ingredient a stock row or a recipe line can name.
    entries = session.execute(
        select(Ingredient.id, Ingredient.parent_id, Ingredient.stock_mode)
    ).all()
    return PlanningContext(
        constraints=constraints,
        stock=stock,
        always_in_stock=always,
        cookware_owned=cookware,
        rules=diner_rules + constraints.guest_rules,
        parents={ingredient_id: parent_id for ingredient_id, parent_id, _ in entries},
        stock_modes={ingredient_id: mode for ingredient_id, _, mode in entries},
    )


def diner_codes(constraints: MealConstraints) -> dict[str, str]:
    """Anonymous codes in diner_ids order, with temporary guests continuing the numbering."""
    keys = [f"diner:{diner_id}" for diner_id in constraints.diner_ids]
    for item in constraints.guest_rules:
        if item.diner_key not in keys:
            keys.append(item.diner_key)
    return {key: f"Diner {number}" for number, key in enumerate(keys, start=1)}
