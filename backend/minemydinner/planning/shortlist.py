"""The shortlist the planner sees first (design 5.4): candidates per needed dish type, with
per-recipe facts, ranked, spread over main ingredients, and cut to a quota.

Stages: fetch (search's exclusions, all_use filter), quota per type, rank (search's order,
with must_use and prefer_use right after query relevance), spread main ingredients with two
protections (named ingredients are left out of the comparison; the spread never leaves too
few makeable dishes or none using a must_use ingredient), and cut.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.llm.client import LLMClient
from minemydinner.models import RecipeIngredient
from minemydinner.planning.composition import CATEGORY_OF_DISH_TYPE, required_counts
from minemydinner.planning.context import PlanningContext
from minemydinner.planning.named import line_uses_clause, recipes_using
from minemydinner.planning.search import RecipeHit, search_recipes

if TYPE_CHECKING:
    from minemydinner.agent.request import ParsedRequest
    from minemydinner.planning.validate import PlanSpec

SHORTLIST_MIN_PER_CATEGORY = 5
SHORTLIST_MARGIN = 3
SHORTLIST_SAME_MAIN = 2

DISH_TYPE_OF_CATEGORY = {category: dish_type for dish_type, category in CATEGORY_OF_DISH_TYPE.items()}
SINGLE_CATEGORIES = ("soups", "staples", "desserts", "snacks")


@dataclass(frozen=True)
class ShortlistEntry:
    """One candidate as the planner sees it: names and diner codes only, no source text."""

    id: int
    title: str
    dish_type: str
    time_min: int | None
    time_source: str | None
    servings: int | None
    missing: list[str]
    missing_cookware: list[str]
    uses_expiring: list[str]
    dislikes: list[str]

    @classmethod
    def from_hit(cls, hit: RecipeHit) -> "ShortlistEntry":
        return cls(
            id=hit.id,
            title=hit.title,
            dish_type=hit.dish_type,
            time_min=hit.time_min,
            time_source=hit.time_source,
            servings=hit.servings,
            missing=list(hit.missing),
            missing_cookware=list(hit.missing_cookware),
            uses_expiring=list(hit.uses_expiring),
            dislikes=list(hit.dislikes),
        )


def needed_by_dish_type(spec: "PlanSpec") -> dict[str, int]:
    """How many dishes of each type the plan may need; types needing none are left out.

    A meat or vegetable side named exactly needs that many; otherwise a side may take the
    dish total less the other side's named count (the whole total when nothing is named),
    so a split naming one side as the whole total shortlists only that side."""
    required = required_counts(spec)
    needed: dict[str, int] = {}
    if spec.dishes >= 1:
        for side, other in (("meat_dishes", "vegetable_dishes"), ("vegetable_dishes", "meat_dishes")):
            named = required.get(side)
            if named is not None and not spec.split_is_minimum:
                count = named
            else:
                count = spec.dishes - (required.get(other) or 0)
            if count >= 1:
                needed[DISH_TYPE_OF_CATEGORY[side]] = count
    for category in SINGLE_CATEGORIES:
        if required[category] >= 1:
            needed[DISH_TYPE_OF_CATEGORY[category]] = required[category]
    return needed


def slots_for(needed: int) -> int:
    return max(SHORTLIST_MIN_PER_CATEGORY, needed + SHORTLIST_MARGIN)


def _names(*groups: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(name for group in groups for name in group if name.strip()))


def main_groups(
    session: Session, ctx: PlanningContext, recipe_ids: list[int], named: Sequence[str]
) -> dict[int, frozenset[int] | None]:
    """Per recipe, its set of main ingredients for the spread: linked `main` lines, each
    compared through its parent entry when it has one, with lines using a named ingredient
    left out. None means the recipe is never grouped: no linked main line, or nothing left
    once the named ingredients are set aside."""
    if not recipe_ids:
        return {}
    is_main = (RecipeIngredient.recipe_id.in_(recipe_ids), RecipeIngredient.role == "main")
    named_lines: set[int] = set()
    for name in named:
        named_lines |= set(
            session.scalars(
                select(RecipeIngredient.id).where(*is_main, line_uses_clause(session, ctx, name))
            )
        )
    sets: dict[int, set[int]] = {}
    rows = session.execute(
        select(RecipeIngredient.id, RecipeIngredient.recipe_id, RecipeIngredient.ingredient_id)
        .where(*is_main, RecipeIngredient.ingredient_id.is_not(None))
    )
    for line_id, recipe_id, ingredient_id in rows:
        members = sets.setdefault(recipe_id, set())
        if line_id not in named_lines:
            members.add(ctx.parents.get(ingredient_id) or ingredient_id)
    return {
        recipe_id: frozenset(sets[recipe_id]) if sets.get(recipe_id) else None
        for recipe_id in recipe_ids
    }


def _spread(
    ranked: list[RecipeHit],
    slots: int,
    needed: int,
    groups: dict[int, frozenset[int] | None],
    makeable: Callable[[RecipeHit], bool],
    must_users: dict[str, set[int]],
) -> list[RecipeHit]:
    """Stage 4 and 5 for one dish type. `ranked` is every candidate in rank order."""
    rank = {hit.id: index for index, hit in enumerate(ranked)}
    kept: list[RecipeHit] = []
    per_group: Counter = Counter()
    for hit in ranked:
        if len(kept) >= slots:
            break
        group = groups.get(hit.id)
        if group is not None and per_group[group] >= SHORTLIST_SAME_MAIN:
            continue
        kept.append(hit)
        if group is not None:
            per_group[group] += 1
    kept_ids = {hit.id for hit in kept}
    # Recipes left out, by the spread or the cut, in rank order.
    others = [hit for hit in ranked if hit.id not in kept_ids]

    def makeable_count() -> int:
        return sum(makeable(hit) for hit in kept)

    def helps(hit: RecipeHit) -> bool:
        """True when dropping `hit` would undo a protection."""
        if makeable(hit) and makeable_count() <= needed:
            return True
        return any(
            hit.id in users and sum(k.id in users for k in kept) == 1
            for users in must_users.values()
        )

    def put_back(hit: RecipeHit) -> None:
        others.remove(hit)
        if len(kept) >= slots:
            victim = next((k for k in reversed(kept) if not helps(k)), None)
            # No victim when every entry is protected, e.g. each of several must_use names
            # has its only user here; the list then exceeds the quota by one.
            if victim is not None:
                kept.remove(victim)
        kept.append(hit)
        kept.sort(key=lambda k: rank[k.id])

    while makeable_count() < needed:
        candidate = next((hit for hit in others if makeable(hit)), None)
        if candidate is None:
            break
        put_back(candidate)
    for users in must_users.values():
        if any(hit.id in users for hit in kept):
            continue
        candidate = next((hit for hit in others if hit.id in users), None)
        if candidate is not None:
            put_back(candidate)
    return kept


@dataclass(frozen=True)
class ShortlistResult:
    """The shortlist plus the notes its search produced, for the plan card."""

    entries: dict[str, list[ShortlistEntry]]
    # Titles of recipes left out because an ingredient's allergens are unknown.
    left_out_unknown_allergens: list[str] = field(default_factory=list)
    # True when meaning-based search was unavailable and the query matched text only.
    semantic_paused: bool = False


def build_shortlist(
    session: Session,
    client: LLMClient,
    ctx: PlanningContext,
    spec: "PlanSpec",
    parsed: "ParsedRequest",
    query_vectors: dict[str, list[float]],
) -> dict[str, list[ShortlistEntry]]:
    """Candidates per needed dish type (see `build_shortlist_result`), without the notes."""
    return build_shortlist_result(session, client, ctx, spec, parsed, query_vectors).entries


def build_shortlist_result(
    session: Session,
    client: LLMClient,
    ctx: PlanningContext,
    spec: "PlanSpec",
    parsed: "ParsedRequest",
    query_vectors: dict[str, list[float]],
) -> ShortlistResult:
    """Candidates per needed dish type, keyed by dish type, each list in rank order.

    Uses the request's query, time limit, and exclude_recent_days as search does; the
    query ranks and never empties a type. all_use keeps only recipes using every named
    ingredient; must_use and prefer_use rank. In fridge-only mode a makeable recipe has no
    missing line and ranks before query relevance; in shopping mode every safe recipe is
    makeable and relevance stays first."""
    needed = needed_by_dish_type(spec)
    if not needed:
        return ShortlistResult({})
    ranking_names = _names(spec.must_use, spec.prefer_use)
    result = search_recipes(
        session,
        client,
        ctx,
        query=parsed.query,
        max_time=spec.time_limit_min,
        must_use=_names(spec.all_use) or None,
        exclude_recent_days=parsed.exclude_recent_days,
        limit=None,
        dish_types=list(needed),
        query_vectors=query_vectors,
        prefer_use=ranking_names,
        keep_unmatched=True,
        makeable_first=spec.mode == "fridge_only",
    )
    hits = result.hits
    recipe_ids = [hit.id for hit in hits]
    groups = main_groups(
        session, ctx, recipe_ids, _names(spec.all_use, spec.must_use, spec.prefer_use)
    )
    must_users = {
        name: recipes_using(session, ctx, name, recipe_ids) for name in _names(spec.must_use)
    }

    def makeable(hit: RecipeHit) -> bool:
        return spec.mode == "shopping" or not hit.missing

    shortlist: dict[str, list[ShortlistEntry]] = {}
    for dish_type, count in needed.items():
        ranked = [hit for hit in hits if hit.dish_type == dish_type]
        kept = _spread(ranked, slots_for(count), count, groups, makeable, must_users)
        shortlist[dish_type] = [ShortlistEntry.from_hit(hit) for hit in kept]
    return ShortlistResult(
        shortlist,
        [excluded.title for excluded in result.left_out_unknown_allergens],
        result.semantic_paused,
    )
