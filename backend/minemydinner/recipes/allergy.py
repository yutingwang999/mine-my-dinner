"""Allergy checks shared by import review (T0.6), tools (T1.1), and validation (T1.2)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.models import DietaryRule, Ingredient


@dataclass(frozen=True)
class AllergenInfo:
    known: frozenset[str]
    # True when any allergen value is missing, so the ingredient cannot be shown to be safe.
    unknown: bool


@dataclass(frozen=True)
class LineRef:
    name: str
    ingredient_id: int | None


@dataclass(frozen=True)
class AllergyConflict:
    diner_id: int
    line_name: str
    allergen: str | None
    # The ingredient the rule names, which may be the line's parent.
    ingredient_id: int | None


def allergen_info(ingredient: Ingredient) -> AllergenInfo:
    """Own allergens plus the parent's, because a child inherits them."""
    sources = [ingredient.allergens]
    if ingredient.parent is not None:
        sources.append(ingredient.parent.allergens)
    known = frozenset(allergen for values in sources if values for allergen in values)
    return AllergenInfo(known=known, unknown=any(values is None for values in sources))


def _ingredients(session: Session, lines: list[LineRef]) -> dict[int, Ingredient]:
    ids = {line.ingredient_id for line in lines if line.ingredient_id is not None}
    if not ids:
        return {}
    rows = session.scalars(select(Ingredient).where(Ingredient.id.in_(ids)))
    return {row.id: row for row in rows}


def find_allergy_conflicts(
    session: Session, diner_ids: list[int], lines: list[LineRef]
) -> list[AllergyConflict]:
    """Lines that break an allergy rule of these diners. Unmatched lines are not checked
    here; find_unknown_allergen_lines reports them."""
    if not diner_ids:
        return []
    rules = session.scalars(
        select(DietaryRule)
        .where(DietaryRule.diner_id.in_(diner_ids), DietaryRule.rule_type == "allergy")
        .order_by(DietaryRule.id)
    ).all()
    ingredients = _ingredients(session, lines)
    conflicts = []
    for line in lines:
        ingredient = ingredients.get(line.ingredient_id) if line.ingredient_id else None
        if ingredient is None:
            continue
        info = allergen_info(ingredient)
        # A rule on a parent covers its children, never the other way round.
        covered = {ingredient.id, ingredient.parent_id} - {None}
        for rule in rules:
            if rule.allergen is not None and rule.allergen in info.known:
                conflicts.append(AllergyConflict(rule.diner_id, line.name, rule.allergen, None))
            elif rule.ingredient_id is not None and rule.ingredient_id in covered:
                conflicts.append(
                    AllergyConflict(rule.diner_id, line.name, None, rule.ingredient_id)
                )
    return conflicts


def find_unknown_allergen_lines(session: Session, lines: list[LineRef]) -> list[str]:
    """Names of lines whose allergens are unknown: unmatched, or with a null value."""
    ingredients = _ingredients(session, lines)
    unknown = []
    for line in lines:
        ingredient = ingredients.get(line.ingredient_id) if line.ingredient_id else None
        if ingredient is None or allergen_info(ingredient).unknown:
            unknown.append(line.name)
    return unknown
