"""The one rule for "this recipe uses the named ingredient" (design 5.1).

Search's must_use filter, validation (H7), the shortlist, and the feasibility check all use
it, so they agree. A line counts when it is linked to the dictionary entry or one of its
children; an unlinked line, or any line when the dictionary does not know the name, counts
when the folded name is contained in the line's name or raw text.
"""

from collections.abc import Iterable

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from minemydinner.extraction.normalize import match_ingredient
from minemydinner.models import Recipe, RecipeIngredient
from minemydinner.planning.context import PlanningContext


def fold(value: str) -> str:
    return value.casefold().strip()


def _text_match(name: str):
    folded = fold(name)
    return or_(
        func.lower(RecipeIngredient.name).contains(folded, autoescape=True),
        func.lower(RecipeIngredient.raw).contains(folded, autoescape=True),
    )


def line_uses_clause(
    session: Session, ctx: PlanningContext, name: str, unresolved: list[str] | None = None
):
    """SQL condition on RecipeIngredient: this line uses the named ingredient. A name the
    dictionary does not know is appended to `unresolved` when a list is given."""
    entry = match_ingredient(session, name)
    if entry is None:
        if unresolved is not None:
            unresolved.append(name)
        return _text_match(name)
    accepted = {entry.id} | {c for c, parent in ctx.parents.items() if parent == entry.id}
    return or_(
        RecipeIngredient.ingredient_id.in_(accepted),
        and_(RecipeIngredient.ingredient_id.is_(None), _text_match(name)),
    )


def uses_clause(
    session: Session, ctx: PlanningContext, name: str, unresolved: list[str] | None = None
):
    """SQL condition on Recipe: some line uses the named ingredient. A name the dictionary
    does not know is appended to `unresolved` when a list is given."""
    match = line_uses_clause(session, ctx, name, unresolved)
    return exists().where(RecipeIngredient.recipe_id == Recipe.id, match)


def recipes_using(
    session: Session, ctx: PlanningContext, name: str, recipe_ids: Iterable[int]
) -> set[int]:
    """The recipes among `recipe_ids` that use the named ingredient."""
    ids = list(recipe_ids)
    if not ids:
        return set()
    return set(
        session.scalars(
            select(Recipe.id).where(Recipe.id.in_(ids), uses_clause(session, ctx, name))
        )
    )
