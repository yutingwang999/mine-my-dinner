"""Dietary rule checks for recipes (design 5.5, H2): allergies, restrictions, dislikes, text rules."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from minemydinner.constants import ALLERGENS
from minemydinner.extraction.normalize import all_terms
from minemydinner.models import Ingredient, RecipeIngredient
from minemydinner.planning.context import RuleRef
from minemydinner.recipes.allergy import allergen_info

SOFT_TYPES = ("dislike",)


@dataclass(frozen=True)
class RuleConflict:
    diner_key: str
    recipe_id: int
    line_name: str
    rule_type: str
    allergen: str | None
    ingredient_id: int | None
    text: str | None


@dataclass
class DietaryCheck:
    hard: list[RuleConflict] = field(default_factory=list)
    dislikes: list[RuleConflict] = field(default_factory=list)
    # Lines whose allergens cannot be shown to be absent: unlinked, or a null allergen value.
    unknown_allergen_lines: dict[int, list[str]] = field(default_factory=dict)
    # Lines with no dictionary entry.
    unrecognized_lines: dict[int, list[str]] = field(default_factory=dict)
    any_allergy: bool = False

    def blocked_by_unknown(self, recipe_id: int) -> bool:
        return self.any_allergy and bool(self.unknown_allergen_lines.get(recipe_id))


def _fold(value: str) -> str:
    return value.casefold().strip()


def _folded_terms(entries: Sequence[Ingredient]) -> set[str]:
    return {_fold(term) for entry in entries for term in all_terms(entry)} - {""}


def _wording_by_ingredient(session: Session, rules: Sequence[RuleRef]) -> dict[int, set[str]]:
    """For each ingredient a rule names: its own wordings and those of its children."""
    ids = {item.ingredient_id for item in rules if item.ingredient_id is not None}
    if not ids:
        return {}
    entries = session.scalars(
        select(Ingredient).where(Ingredient.id.in_(ids) | Ingredient.parent_id.in_(ids))
    ).all()
    wording = {}
    for ingredient_id in ids:
        related = [e for e in entries if e.id == ingredient_id or e.parent_id == ingredient_id]
        wording[ingredient_id] = _folded_terms(related)
    return wording


def _hits(
    item: RuleRef,
    line: RecipeIngredient,
    wording: dict[int, set[str]],
) -> bool:
    ingredient = line.ingredient
    covered = {ingredient.id, ingredient.parent_id} - {None} if ingredient else set()
    if item.allergen is not None and ingredient is not None:
        if item.allergen in allergen_info(ingredient).known:
            return True
    if item.ingredient_id is not None:
        if item.ingredient_id in covered:
            return True
        if ingredient is None:
            # Source wording: any term of the ingredient or its children inside the name or raw.
            haystacks = (_fold(line.name), _fold(line.raw))
            if any(t in h for t in wording.get(item.ingredient_id, set()) for h in haystacks):
                return True
    text = _fold(item.text) if item.text else ""
    if text:
        if text in _fold(line.name) or text in _fold(line.raw):
            return True
        if ingredient is not None:
            entries = [ingredient] + ([ingredient.parent] if ingredient.parent else [])
            if any(text in term for term in _folded_terms(entries)):
                return True
    return False


def check_recipes(
    session: Session, rules: Sequence[RuleRef], recipe_ids: list[int]
) -> DietaryCheck:
    """Check every line of these recipes (optional and choice-group lines too) against the rules."""
    for item in rules:
        if item.allergen is not None and item.allergen not in ALLERGENS:
            raise ValueError(f"Unknown allergen: {item.allergen}")
    check = DietaryCheck(any_allergy=any(item.rule_type == "allergy" for item in rules))
    if not recipe_ids:
        return check
    lines = session.scalars(
        select(RecipeIngredient)
        .where(RecipeIngredient.recipe_id.in_(recipe_ids))
        .options(selectinload(RecipeIngredient.ingredient).selectinload(Ingredient.parent))
        .order_by(RecipeIngredient.recipe_id, RecipeIngredient.id)
    ).all()
    wording = _wording_by_ingredient(session, rules)
    for line in lines:
        if line.ingredient is None:
            check.unrecognized_lines.setdefault(line.recipe_id, []).append(line.name)
        if line.ingredient is None or allergen_info(line.ingredient).unknown:
            check.unknown_allergen_lines.setdefault(line.recipe_id, []).append(line.name)
        for item in rules:
            if not _hits(item, line, wording):
                continue
            conflict = RuleConflict(
                item.diner_key,
                line.recipe_id,
                line.name,
                item.rule_type,
                item.allergen,
                item.ingredient_id,
                item.text,
            )
            (check.dislikes if item.rule_type in SOFT_TYPES else check.hard).append(conflict)
    return check
