"""The feasibility check (design 5.2): before any model call, decide whether a meal request
can be met from the recipe library (and the fridge in fridge-only mode), and if not, why.

It looks at the whole library, not the shortlist. "Safe" is exactly what search returns
(H2 and unknown-allergen exclusions). When in doubt the request passes: a recipe with no
firm time fits any time limit, and a `must_use` name the dictionary does not know skips the
stock check.
"""

from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from minemydinner.extraction.normalize import match_ingredient
from minemydinner.planning.composition import CATEGORY_OF_DISH_TYPE, required_counts
from minemydinner.planning.context import PlanningContext
from minemydinner.planning.named import recipes_using
from minemydinner.planning.search import FIRM_TIME_SOURCES, RecipeHit, search_recipes
from minemydinner.planning.stock import is_available
from minemydinner.planning.validate import PlanSpec

REASONS = (
    "empty_inventory",
    "no_cookware",
    "not_enough_dishes",
    "time_limit",
    "not_enough_in_library",
    "named_ingredient",
)

_DISH_TYPE_OF_CATEGORY = {category: dish_type for dish_type, category in CATEGORY_OF_DISH_TYPE.items()}
_SINGLE_CATEGORIES = ("soups", "staples", "desserts", "snacks")
_SIDES = ("meat_dishes", "vegetable_dishes")


@dataclass(frozen=True)
class Shortfall:
    """One reason the request cannot be met. `category` is a key of required_counts, or
    "dishes" for meat and vegetable dishes combined, or None when no category applies.
    `ingredient` is set for named_ingredient (all names joined by ", " when several)."""

    reason: str
    category: str | None
    needed: int | None
    available: int | None
    ingredient: str | None = None
    # True when the needed count was parsed as a minimum ("at least").
    minimum: bool = False
    # named_ingredient in fridge-only mode: the ingredient is not in stock at all.
    absent_from_stock: bool = False


@dataclass(frozen=True)
class _Unit:
    category: str
    dish_types: tuple[str, ...]
    needed: int


def _units(spec: PlanSpec) -> list[_Unit]:
    required = required_counts(spec)
    units: list[_Unit] = []
    for category in _SINGLE_CATEGORIES:
        if required[category] >= 1:
            units.append(_Unit(category, (_DISH_TYPE_OF_CATEGORY[category],), required[category]))
    # Named sides are checked on their own (a minimum compares the same way); the dish total
    # is checked across both types so an unnamed side can take the rest.
    for side in _SIDES:
        if required.get(side, 0) >= 1:
            units.append(_Unit(side, (_DISH_TYPE_OF_CATEGORY[side],), required[side]))
    if spec.dishes >= 1:
        units.append(
            _Unit("dishes", tuple(_DISH_TYPE_OF_CATEGORY[side] for side in _SIDES), spec.dishes)
        )
    return units


def _fits_time(hit: RecipeHit, limit: int) -> bool:
    firm = hit.time_source in FIRM_TIME_SOURCES and hit.time_min is not None
    return not firm or hit.time_min <= limit


def _has_inventory(ctx: PlanningContext) -> bool:
    return any(ingredient_id not in ctx.always_in_stock for ingredient_id in ctx.stock)


def check_feasibility(session: Session, ctx: PlanningContext, spec: PlanSpec) -> list[Shortfall]:
    """Every reason the request cannot be met, in REASONS order; empty means "plan it"."""
    found: list[Shortfall] = []
    stock_checks = spec.mode == "fridge_only"

    units = _units(spec)
    dish_types = sorted({dish_type for unit in units for dish_type in unit.dish_types})
    hits = (
        search_recipes(session, None, ctx, limit=None, dish_types=dish_types).hits  # type: ignore[arg-type]
        if dish_types
        else []
    )

    # Always-in-stock items alone can still make a dish, so an empty fridge is a stop only
    # when nothing is makeable either. Then the stock-based checks would only repeat it.
    if stock_checks and not _has_inventory(ctx) and all(hit.missing for hit in hits):
        found.append(Shortfall("empty_inventory", None, None, None))
        stock_checks = False

    def pool(unit: _Unit, makeable_only: bool = False) -> list[RecipeHit]:
        return [
            hit
            for hit in hits
            if hit.dish_type in unit.dish_types and not (makeable_only and hit.missing)
        ]

    for unit in units:
        safe = pool(unit)
        if len(safe) < unit.needed:
            found.append(Shortfall("not_enough_in_library", unit.category, unit.needed, len(safe)))
        elif stock_checks:
            makeable = pool(unit, makeable_only=True)
            if len(makeable) < unit.needed:
                found.append(
                    Shortfall("not_enough_dishes", unit.category, unit.needed, len(makeable))
                )
        if not safe:
            continue
        if not any(not hit.missing_cookware for hit in safe):
            found.append(Shortfall("no_cookware", unit.category, unit.needed, 0))
        # In fridge-only mode only makeable recipes reach the shortlist and search, so the
        # time limit is judged on them. With none makeable (already not_enough_dishes), it
        # is judged on every safe recipe, so the user still learns that shopping alone
        # would not meet the limit.
        timed = (pool(unit, makeable_only=True) if stock_checks else []) or safe
        if spec.time_limit_min is not None and not any(
            _fits_time(hit, spec.time_limit_min) for hit in timed
        ):
            found.append(Shortfall("time_limit", unit.category, unit.needed, 0))

    found = _drop_combined_duplicates(found)
    found.extend(_named_shortfalls(session, ctx, spec, units, pool, stock_checks))
    if spec.split_is_minimum:
        found = [replace(s, minimum=True) if s.category in _SIDES else s for s in found]
    return sorted(found, key=lambda shortfall: REASONS.index(shortfall.reason))


def _drop_combined_duplicates(found: list[Shortfall]) -> list[Shortfall]:
    """When a named side already reports a reason, the combined "dishes" line adds nothing."""
    by_side = {s.reason for s in found if s.category in _SIDES}
    return [s for s in found if not (s.category == "dishes" and s.reason in by_side)]


def _unavailable(session: Session, ctx: PlanningContext, name: str) -> bool:
    """True when fridge-only stock cannot supply the named ingredient. A name the
    dictionary does not know is not judged here (pass through)."""
    entry = match_ingredient(session, name)
    # Seasoning role: the most lenient reading of "held", as compute_missing applies it.
    return entry is not None and not is_available(ctx, entry.id, "seasoning")


def _named_shortfalls(
    session: Session,
    ctx: PlanningContext,
    spec: PlanSpec,
    units: list[_Unit],
    pool,
    stock_checks: bool,
) -> list[Shortfall]:
    found: list[Shortfall] = []
    if not units:
        return found
    must = [name for name in dict.fromkeys(spec.must_use) if name.strip()]
    every = [name for name in dict.fromkeys(spec.all_use) if name.strip()]

    for name in [*must, *every]:
        if stock_checks and _unavailable(session, ctx, name):
            found.append(
                Shortfall("named_ingredient", None, None, None, name, absent_from_stock=True)
            )
    unavailable = {s.ingredient for s in found}

    for name in must:
        if name in unavailable:
            continue
        # A name the dictionary does not know is judged on safe recipes, not makeable ones.
        known = match_ingredient(session, name) is not None
        makeable_only = stock_checks and known
        ids = {hit.id for unit in units for hit in pool(unit, makeable_only)}
        # An empty pool is already a library shortfall, not a named-ingredient one.
        if ids and not recipes_using(session, ctx, name, ids):
            found.append(Shortfall("named_ingredient", None, None, None, name))

    if every and not unavailable.intersection(every):
        label = ", ".join(every)
        for unit in units:
            if not pool(unit):
                continue  # nothing to count: a library shortfall, reported elsewhere
            ids = {hit.id for hit in pool(unit, stock_checks)}
            for name in every:
                ids = recipes_using(session, ctx, name, ids)
            if len(ids) < unit.needed:
                found.append(
                    Shortfall("named_ingredient", unit.category, unit.needed, len(ids), label)
                )
        side_named = any(
            s.reason == "named_ingredient" and s.category in _SIDES for s in found
        )
        if side_named:
            found = [s for s in found if not (s.category == "dishes" and s.reason == "named_ingredient")]
    return found

