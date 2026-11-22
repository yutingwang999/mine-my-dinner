from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.models import (
    AlwaysInStock,
    CookwareOwned,
    DietaryRule,
    Ingredient,
    Inventory,
)

if TYPE_CHECKING:
    from minemydinner.agent.labels import LabelTable


# The diner key of this meal's own rules: a one-meal avoidance ("今天不想吃肉", plan 3b-1b
# spec 5.4). It is no person: it never gets a "Diner N" label and never reaches a profile.
MEAL_KEY = "meal"
# How the meal's own rules are named to a model, in place of a diner code.
MEAL_CODE = "This meal"


@dataclass(frozen=True)
class RuleRef:
    """One dietary rule. `diner_key` is "diner:<id>", "guest:<n>" for a temporary guest, or
    MEAL_KEY for this meal's own rule (carried in `MealConstraints.guest_rules`)."""

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
    # The one-meal avoidance as the user said it ("肉"); its rules are in guest_rules under
    # MEAL_KEY, one per ingredient for a group word, so models are told these words instead.
    meal_avoid: tuple[str, ...] = ()


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


def diner_codes(
    constraints: MealConstraints, table: "LabelTable | None" = None
) -> dict[str, str]:
    """Anonymous codes for the selected diners, then temporary guests.

    With the conversation's label table, each key gets its label there (new keys are
    assigned the next number). Without one, codes follow diner_ids order, with guests
    continuing the numbering. This meal's own rules are no person: their key gets MEAL_CODE
    and never a label.
    """
    keys = [f"diner:{diner_id}" for diner_id in constraints.diner_ids]
    meal = False
    for item in constraints.guest_rules:
        if item.diner_key == MEAL_KEY:
            meal = True
        elif item.diner_key not in keys:
            keys.append(item.diner_key)
    if table is not None:
        codes = {key: table.label_for(key) for key in keys}
    else:
        codes = {key: f"Diner {number}" for number, key in enumerate(keys, start=1)}
    if meal:
        codes[MEAL_KEY] = MEAL_CODE
    return codes
