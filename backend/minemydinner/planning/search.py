"""Recipe search for the planner: filters and ranking in code, exclusions never left to the model."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from minemydinner.extraction.normalize import all_terms, match_ingredient
from minemydinner.llm.client import AllModelsFailed, LLMClient, MonthlyCapExceeded
from minemydinner.models import MealPlan, MealPlanItem, Rating, Recipe, RecipeIngredient
from minemydinner.planning.context import PlanningContext
from minemydinner.planning.dietary import check_recipes

# Only a time the source or the user gave can rule a recipe out (H6); an estimate cannot.
FIRM_TIME_SOURCES = ("stated", "user")


@dataclass(frozen=True)
class RecipeHit:
    id: int
    title: str
    dish_type: str
    time_min: int | None
    time_source: str | None
    tags: list[str]
    servings: int | None
    rating: int | None


@dataclass(frozen=True)
class ExcludedRecipe:
    id: int
    title: str
    lines: list[str]


@dataclass(frozen=True)
class SearchResult:
    hits: list[RecipeHit]
    left_out_unknown_allergens: list[ExcludedRecipe] = field(default_factory=list)
    semantic_paused: bool = False
    # must_use names the dictionary does not know; they matched by line wording only.
    unresolved_must_use: list[str] = field(default_factory=list)


def _fold(value: str) -> str:
    return value.casefold().strip()


def _must_use_clause(session: Session, ctx: PlanningContext, name: str, unresolved: list[str]):
    """A line linked to the named ingredient or to one of its children. A name the
    dictionary does not know falls back to the line wording."""
    entry = match_ingredient(session, name)
    if entry is None:
        unresolved.append(name)
        match = func.lower(RecipeIngredient.name).contains(_fold(name), autoescape=True)
    else:
        accepted = {entry.id} | {c for c, parent in ctx.parents.items() if parent == entry.id}
        match = RecipeIngredient.ingredient_id.in_(accepted)
    return exists().where(RecipeIngredient.recipe_id == Recipe.id, match)


def _line_terms(session: Session, recipe_ids: list[int]) -> dict[int, list[str]]:
    """Folded line wording per recipe, plus every name, alias, and personal alias of the
    linked dictionary entries."""
    terms: dict[int, list[str]] = {recipe_id: [] for recipe_id in recipe_ids}
    rows = session.scalars(
        select(RecipeIngredient)
        .where(RecipeIngredient.recipe_id.in_(recipe_ids))
        .options(selectinload(RecipeIngredient.ingredient))
    )
    for row in rows:
        terms[row.recipe_id].append(_fold(row.name))
        if row.ingredient is not None:
            terms[row.recipe_id].extend(_fold(term) for term in all_terms(row.ingredient))
    return terms


def search_recipes(
    session: Session,
    client: LLMClient,
    ctx: PlanningContext,
    query: str | None = None,
    max_time: int | None = None,
    tags: list[str] | None = None,
    must_use: list[str] | None = None,
    exclude_recent_days: int | None = None,
    limit: int = 20,
) -> SearchResult:
    """Search the recipe library. Allergy and dietary exclusions always apply.

    tags are a preference, not a filter: recipes whose tags or title contain a requested
    tag rank first, and recipes without one are still returned. max_time drops only
    recipes whose stated or user-given time is over the limit. must_use needs a line for
    each named ingredient or one of its children. query ranks by meaning when the
    embedding service is available and by text otherwise.
    """
    candidates = (
        select(
            Recipe.id,
            Recipe.title,
            Recipe.tags,
            Recipe.dish_type,
            Recipe.time_min,
            Recipe.time_source,
            Recipe.servings,
            Rating.score,
        )
        .outerjoin(Rating, Rating.recipe_id == Recipe.id)
        .order_by(Rating.score.desc().nulls_last(), Recipe.created_at.desc(), Recipe.id.desc())
    )
    if max_time is not None:
        candidates = candidates.where(
            or_(
                Recipe.time_min.is_(None),
                Recipe.time_source.is_(None),
                Recipe.time_source.not_in(FIRM_TIME_SOURCES),
                Recipe.time_min <= max_time,
            )
        )
    unresolved: list[str] = []
    for name in must_use or []:
        if name.strip():
            candidates = candidates.where(_must_use_clause(session, ctx, name, unresolved))
    if exclude_recent_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=exclude_recent_days)
        recent = (
            select(MealPlanItem.recipe_id)
            .join(MealPlan, MealPlan.id == MealPlanItem.plan_id)
            .where(MealPlan.confirmed_at >= since, MealPlanItem.recipe_id.is_not(None))
        )
        candidates = candidates.where(Recipe.id.not_in(recent))
    by_id = {row.id: row for row in session.execute(candidates)}
    ordered = list(by_id)

    semantic_paused = False
    if query and query.strip() and ordered:
        ordered, semantic_paused = _rank_by_query(session, client, query, by_id)

    wanted = [_fold(tag) for tag in tags or [] if tag.strip()]
    if wanted:

        def boosted(recipe_id: int) -> bool:
            row = by_id[recipe_id]
            texts = [_fold(row.title), *(_fold(tag) for tag in row.tags)]
            return any(tag in text for tag in wanted for text in texts)

        # A stable partition: matching recipes first, every other recipe kept in order.
        ordered = [i for i in ordered if boosted(i)] + [i for i in ordered if not boosted(i)]

    check = check_recipes(session, ctx.rules, ordered)
    hard = {conflict.recipe_id for conflict in check.hard}
    hits: list[RecipeHit] = []
    left_out: list[ExcludedRecipe] = []
    for recipe_id in ordered:
        if recipe_id in hard:
            continue
        row = by_id[recipe_id]
        if check.blocked_by_unknown(recipe_id):
            left_out.append(
                ExcludedRecipe(recipe_id, row.title, check.unknown_allergen_lines[recipe_id])
            )
        elif len(hits) < limit:
            hits.append(
                RecipeHit(
                    id=recipe_id,
                    title=row.title,
                    dish_type=row.dish_type,
                    time_min=row.time_min,
                    time_source=row.time_source,
                    tags=list(row.tags),
                    servings=row.servings,
                    rating=row.score,
                )
            )
    return SearchResult(hits, left_out, semantic_paused, unresolved)


def _rank_by_query(
    session: Session, client: LLMClient, query: str, by_id: dict
) -> tuple[list[int], bool]:
    """Vector order among embedded recipes, then text matches without an embedding. When
    the query cannot be embedded, only text matches remain."""
    ordered = list(by_id)
    try:
        vector = client.embed([query])[0]
    except (MonthlyCapExceeded, AllModelsFailed):
        vector = None
    embedded: list[int] = []
    if vector is not None:
        embedded = list(
            session.scalars(
                select(Recipe.id)
                .where(Recipe.id.in_(ordered), Recipe.embedding.is_not(None))
                .order_by(Recipe.embedding.cosine_distance(vector), Recipe.id)
            )
        )
    ranked = set(embedded)
    rest = [recipe_id for recipe_id in ordered if recipe_id not in ranked]
    words = _fold(query).split()
    terms = _line_terms(session, rest)

    def score(recipe_id: int) -> int:
        texts = [
            _fold(by_id[recipe_id].title),
            *(_fold(tag) for tag in by_id[recipe_id].tags),
            *terms[recipe_id],
        ]
        return sum(any(word in text for text in texts) for word in words)

    scores = {recipe_id: score(recipe_id) for recipe_id in rest}
    # sorted is stable, so equal scores keep the rating and recency order.
    matched = sorted((i for i in rest if scores[i] > 0), key=lambda i: -scores[i])
    return embedded + matched, vector is None


def recipe_detail(session: Session, recipe_id: int) -> dict | None:
    """The recipe as the planner may read it: no full method text and no images."""
    recipe = session.get(Recipe, recipe_id)
    if recipe is None:
        return None
    return {
        "id": recipe.id,
        "title": recipe.title,
        "dish_type": recipe.dish_type,
        "time_min": recipe.time_min,
        "time_source": recipe.time_source,
        "servings": recipe.servings,
        "tags": list(recipe.tags),
        "supplies": list(recipe.supplies),
        "steps_summary": recipe.steps_summary,
        "source_url": recipe.source_url,
        "lines": [
            {
                "raw": line.raw,
                "name": line.name,
                "ingredient_id": line.ingredient_id,
                "amount": line.amount,
                "unit": line.unit,
                "amount_type": line.amount_type,
                "role": line.role,
                "optional": line.optional,
                "choice_group": line.choice_group,
            }
            for line in recipe.ingredients
        ],
        "cookware": [
            item.cookware.name_zh or item.cookware.name_en or "" for item in recipe.cookware
        ],
    }
