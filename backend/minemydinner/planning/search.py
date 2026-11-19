"""Recipe search for the planner: filters and ranking in code, exclusions never left to the model."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from minemydinner.extraction.normalize import all_terms, match_ingredient
from minemydinner.llm.client import AllModelsFailed, LLMClient, MonthlyCapExceeded
from minemydinner.models import MealPlan, MealPlanItem, Rating, Recipe, RecipeIngredient
from minemydinner.agent.labels import current_table
from minemydinner.planning.context import PlanningContext, diner_codes
from minemydinner.planning.dietary import DietaryCheck, check_recipes
from minemydinner.planning.named import recipes_using, uses_clause
from minemydinner.planning.stock import compute_missing, missing_cookware
from minemydinner.planning.validate import EXPIRY_SOON_DAYS

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
    # Ingredient and cookware names and diner codes only, never a person's name.
    missing: list[str] = field(default_factory=list)
    missing_cookware: list[str] = field(default_factory=list)
    uses_expiring: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BlockedHit(RecipeHit):
    """A hit of a search with `show_blocked`: the hard-rule conflicts (diner keys, never a
    name) and the unknown-allergen lines that would keep it out of a model's search."""

    conflicts: list[dict] = field(default_factory=list)
    blocked_unknown: list[str] = field(default_factory=list)


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


@dataclass(frozen=True)
class _Facts:
    missing: list[str]
    missing_cookware: list[str]
    uses_expiring: list[str]
    dislikes: list[str]


def _fold(value: str) -> str:
    return value.casefold().strip()


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


def unknown_allergen_exclusions(
    session: Session,
    ctx: PlanningContext,
    recipe_ids: list[int] | None = None,
    *,
    dish_types: Sequence[str] | None = None,
    check: DietaryCheck | None = None,
    titles: dict[int, str] | None = None,
) -> list[ExcludedRecipe]:
    """The recipes left out because an ingredient line's allergens are unknown, with those
    lines. `recipe_ids` limits them to those recipes (default: the whole library) and
    `dish_types` to recipes of those types. `check` and `titles` are reused when the caller
    already has them. Empty when no diner of the meal has an allergy. A recipe that breaks a
    hard rule is not listed: it is excluded outright."""
    if not any(item.rule_type == "allergy" for item in ctx.rules):
        return []
    if recipe_ids is None:
        query = select(Recipe.id).order_by(Recipe.id)
        if dish_types is not None:
            query = query.where(Recipe.dish_type.in_(list(dish_types)))
        recipe_ids = list(session.scalars(query))
    if not recipe_ids:
        return []
    if titles is None or any(i not in titles for i in recipe_ids):
        rows = session.execute(select(Recipe.id, Recipe.title).where(Recipe.id.in_(recipe_ids)))
        titles = {row.id: row.title for row in rows}
    if check is None:
        check = check_recipes(session, ctx.rules, recipe_ids)
    hard = {conflict.recipe_id for conflict in check.hard}
    return [
        ExcludedRecipe(i, titles[i], check.unknown_allergen_lines[i])
        for i in recipe_ids
        if i in titles and i not in hard and check.blocked_by_unknown(i)
    ]


def search_recipes(
    session: Session,
    client: LLMClient | None,
    ctx: PlanningContext,
    query: str | None = None,
    max_time: int | None = None,
    tags: list[str] | None = None,
    must_use: list[str] | None = None,
    exclude_recent_days: int | None = None,
    limit: int | None = 20,
    dish_types: Sequence[str] | None = None,
    exclude_ids: Sequence[int] = (),
    query_vectors: dict[str, list[float]] | None = None,
    prefer_use: Sequence[str] = (),
    keep_unmatched: bool = False,
    makeable_only: bool = False,
    show_blocked: bool = False,
    only_ids: Sequence[int] | None = None,
) -> SearchResult:
    """Search the recipe library. Allergy and dietary exclusions always apply.

    tags are a preference, not a filter: recipes whose tags or title contain a requested
    tag rank first, and recipes without one are still returned. max_time drops only
    recipes whose stated or user-given time is over the limit. must_use needs a line for
    each named ingredient or one of its children. dish_types and exclude_ids filter.
    query ranks by meaning when the embedding service is available and by text otherwise;
    a recipe the query does not reach is dropped, or ranked last with keep_unmatched.
    makeable_only (fridge-only mode) drops every recipe with a missing required line.
    query_vectors caches query embeddings for one planning run, so a repeated query is
    embedded once. show_blocked (the plan card's search, never the planner's) keeps the
    recipes a hard rule or an unknown allergen excludes, as BlockedHit with those markers.
    only_ids limits the search to those recipes (the card's text match). client is needed
    only with a query; without one no model is called.

    Every hit carries its missing lines, missing cookware, the close-to-expiry stock it
    uses, and dislike hits. Order: requested tags first; then query relevance; then more
    prefer_use ingredients used, fewer missing lines, more close-to-expiry ingredients
    used, no dislike before a dislike, and, only when max_time is set, a firm time before
    an estimate; then rating and recency. limit=None returns every hit.
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
    if dish_types is not None:
        candidates = candidates.where(Recipe.dish_type.in_(list(dish_types)))
    if only_ids is not None:
        candidates = candidates.where(Recipe.id.in_(list(only_ids)))
    if exclude_ids:
        candidates = candidates.where(Recipe.id.not_in(list(exclude_ids)))
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
            candidates = candidates.where(uses_clause(session, ctx, name, unresolved))
    if exclude_recent_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=exclude_recent_days)
        recent = (
            select(MealPlanItem.recipe_id)
            .join(MealPlan, MealPlan.id == MealPlanItem.plan_id)
            .where(MealPlan.confirmed_at >= since, MealPlanItem.recipe_id.is_not(None))
        )
        candidates = candidates.where(Recipe.id.not_in(recent))
    by_id = {row.id: row for row in session.execute(candidates)}
    # Rating, then recency: the last tie-breaker.
    position = {recipe_id: index for index, recipe_id in enumerate(by_id)}

    semantic_paused = False
    relevance: dict[int, tuple] = {recipe_id: () for recipe_id in by_id}
    if query and query.strip() and by_id:
        assert client is not None, "a query is ranked through the model client"
        relevance, semantic_paused = _rank_by_query(
            session, client, query, by_id, query_vectors, keep_unmatched
        )

    wanted = [_fold(tag) for tag in tags or [] if tag.strip()]

    def tag_tier(recipe_id: int) -> int:
        row = by_id[recipe_id]
        texts = [_fold(row.title), *(_fold(tag) for tag in row.tags)]
        return 0 if any(tag in text for tag in wanted for text in texts) else 1

    tier = {recipe_id: tag_tier(recipe_id) if wanted else 0 for recipe_id in relevance}
    reached = sorted(relevance, key=lambda i: (tier[i], relevance[i], position[i]))

    check = check_recipes(session, ctx.rules, reached)
    hard = {conflict.recipe_id for conflict in check.hard}
    if show_blocked:
        # Nothing is left out: the blocked recipes are hits, with their markers.
        left_out: list[ExcludedRecipe] = []
        safe = list(reached)
    else:
        left_out = unknown_allergen_exclusions(
            session, ctx, reached, check=check, titles={i: row.title for i, row in by_id.items()}
        )
        safe = [i for i in reached if i not in hard and not check.blocked_by_unknown(i)]
    facts = _facts(session, ctx, safe, check)
    if makeable_only:
        safe = [i for i in safe if not facts[i].missing]
    preferred = dict.fromkeys(safe, 0)
    for name in dict.fromkeys(n for n in prefer_use if n.strip()):
        for recipe_id in recipes_using(session, ctx, name, safe):
            preferred[recipe_id] += 1

    def rank(recipe_id: int) -> tuple:
        fact = facts[recipe_id]
        estimated = max_time is not None and by_id[recipe_id].time_source not in FIRM_TIME_SOURCES
        return (
            tier[recipe_id],
            relevance[recipe_id],
            -preferred[recipe_id],
            len(fact.missing),
            -len(fact.uses_expiring),
            bool(fact.dislikes),
            estimated,
            position[recipe_id],
        )

    ordered = sorted(safe, key=rank)
    if limit is not None:
        ordered = ordered[:limit]
    blocked: dict[int, dict] = {}
    if show_blocked:
        for recipe_id in ordered:
            conflicts: list[dict] = []
            for c in check.hard:
                item = {
                    "diner_key": c.diner_key,
                    "rule_type": c.rule_type,
                    "allergen": c.allergen,
                    "ingredient_id": c.ingredient_id,
                    "text": c.text,
                    "line": c.line_name,
                }
                if c.recipe_id == recipe_id and item not in conflicts:
                    conflicts.append(item)
            unknown = check.unknown_allergen_lines.get(recipe_id, [])
            blocked[recipe_id] = {
                "conflicts": conflicts,
                "blocked_unknown": list(unknown) if check.blocked_by_unknown(recipe_id) else [],
            }
    hit_class = BlockedHit if show_blocked else RecipeHit
    hits = [
        hit_class(
            id=recipe_id,
            title=by_id[recipe_id].title,
            dish_type=by_id[recipe_id].dish_type,
            time_min=by_id[recipe_id].time_min,
            time_source=by_id[recipe_id].time_source,
            tags=list(by_id[recipe_id].tags),
            servings=by_id[recipe_id].servings,
            rating=by_id[recipe_id].score,
            missing=facts[recipe_id].missing,
            missing_cookware=facts[recipe_id].missing_cookware,
            uses_expiring=facts[recipe_id].uses_expiring,
            dislikes=facts[recipe_id].dislikes,
            **blocked.get(recipe_id, {}),
        )
        for recipe_id in ordered
    ]
    return SearchResult(hits, left_out, semantic_paused, unresolved)


def _facts(
    session: Session, ctx: PlanningContext, recipe_ids: list[int], check: DietaryCheck
) -> dict[int, _Facts]:
    """Per recipe: missing line names, missing cookware names, the line names met by stock
    close to expiry, and dislike hits as "Diner N: line". A fixed number of queries."""
    if not recipe_ids:
        return {}
    missing = compute_missing(session, ctx, recipe_ids)
    cookware = missing_cookware(session, ctx, recipe_ids)
    expiring: dict[int, list[str]] = {recipe_id: [] for recipe_id in recipe_ids}
    covered = _expiring_needs(ctx)
    if covered:
        rows = session.execute(
            select(RecipeIngredient.recipe_id, RecipeIngredient.name)
            .where(
                RecipeIngredient.recipe_id.in_(recipe_ids),
                RecipeIngredient.ingredient_id.in_(covered),
            )
            .order_by(RecipeIngredient.recipe_id, RecipeIngredient.id)
        )
        for recipe_id, name in rows:
            if name not in expiring[recipe_id]:
                expiring[recipe_id].append(name)
    codes = diner_codes(ctx.constraints, current_table())
    dislikes: dict[int, list[str]] = {recipe_id: [] for recipe_id in recipe_ids}
    for conflict in check.dislikes:
        if conflict.recipe_id not in dislikes:
            continue
        text = f"{codes.get(conflict.diner_key, 'Diner')}: {conflict.line_name}"
        if text not in dislikes[conflict.recipe_id]:
            dislikes[conflict.recipe_id].append(text)
    return {
        recipe_id: _Facts(
            missing=[line.name for line in missing[recipe_id]],
            missing_cookware=[name for _, name in cookware[recipe_id]],
            uses_expiring=expiring[recipe_id],
            dislikes=dislikes[recipe_id],
        )
        for recipe_id in recipe_ids
    }


def _expiring_needs(ctx: PlanningContext) -> set[int]:
    """The ingredient ids a recipe line can name and be met by stock close to expiry: each
    such stocked entry and its ancestors, as S3 counts them. Only entered dates count."""
    cutoff = date.today() + timedelta(days=EXPIRY_SOON_DAYS)
    covered: set[int] = set()
    for ingredient_id, item in ctx.stock.items():
        if item.expires_on is None or item.expires_on > cutoff:
            continue
        current: int | None = ingredient_id
        while current is not None and current not in covered:
            covered.add(current)
            current = ctx.parents.get(current)
    return covered


def _rank_by_query(
    session: Session,
    client: LLMClient,
    query: str,
    by_id: dict,
    query_vectors: dict[str, list[float]] | None = None,
    keep_unmatched: bool = False,
) -> tuple[dict[int, tuple], bool]:
    """A relevance key for each recipe the query reaches (lower ranks first): vector
    distance among embedded recipes, then text matches without an embedding by match
    count, then, with keep_unmatched, every other recipe. When the query cannot be
    embedded, only text matches are reached. A vector found in query_vectors is reused;
    a newly embedded one is stored there."""
    ordered = list(by_id)
    vector = query_vectors.get(query) if query_vectors is not None else None
    if vector is None:
        try:
            vector = client.embed([query])[0]
        except (MonthlyCapExceeded, AllModelsFailed):
            vector = None
        if vector is not None and query_vectors is not None:
            query_vectors[query] = vector
    relevance: dict[int, tuple] = {}
    if vector is not None:
        distance = Recipe.embedding.cosine_distance(vector)
        rows = session.execute(
            select(Recipe.id, distance).where(
                Recipe.id.in_(ordered), Recipe.embedding.is_not(None)
            )
        )
        for recipe_id, value in rows:
            relevance[recipe_id] = (0, float(value))
    rest = [recipe_id for recipe_id in ordered if recipe_id not in relevance]
    words = _fold(query).split()
    terms = _line_terms(session, rest)

    def score(recipe_id: int) -> int:
        texts = [
            _fold(by_id[recipe_id].title),
            *(_fold(tag) for tag in by_id[recipe_id].tags),
            *terms[recipe_id],
        ]
        return sum(any(word in text for text in texts) for word in words)

    for recipe_id in rest:
        matches = score(recipe_id)
        if matches > 0:
            relevance[recipe_id] = (1, -float(matches))
        elif keep_unmatched:
            relevance[recipe_id] = (2, 0.0)
    return relevance, vector is None


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
