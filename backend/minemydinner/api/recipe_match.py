"""The plain text match of recipes, shared by the library page and the plan card's search:
no model call, only recipes whose text contains the query."""

from sqlalchemy import ColumnElement, exists, func, or_, select

from minemydinner.api.deps import contains_pattern
from minemydinner.models import Ingredient, Recipe, RecipeIngredient


def recipe_text_match(q: str) -> ColumnElement[bool]:
    """Recipes whose title, tags, ingredient line names, or linked dictionary entry (its
    names and aliases) contain q, case-insensitively."""
    pattern = contains_pattern(q)
    names = func.array_to_string(
        func.array_cat(
            func.array_cat(Ingredient.aliases_zh, Ingredient.aliases_en),
            func.array_cat(Ingredient.user_aliases_zh, Ingredient.user_aliases_en),
        ),
        "\n",
    )
    line_matches = (
        select(RecipeIngredient.id)
        .outerjoin(Ingredient, RecipeIngredient.ingredient_id == Ingredient.id)
        .where(
            RecipeIngredient.recipe_id == Recipe.id,
            or_(
                RecipeIngredient.name.ilike(pattern),
                # Dictionary names let "egg" find a recipe whose line says "鸡蛋".
                Ingredient.name_zh.ilike(pattern),
                Ingredient.name_en.ilike(pattern),
                names.ilike(pattern),
            ),
        )
    )
    return or_(
        Recipe.title.ilike(pattern),
        func.array_to_string(Recipe.tags, "\n").ilike(pattern),
        exists(line_matches),
    )


def title_contains(q: str) -> ColumnElement[bool]:
    return Recipe.title.ilike(contains_pattern(q))
