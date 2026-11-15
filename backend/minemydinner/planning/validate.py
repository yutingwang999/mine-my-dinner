"""validate_plan (design 7.5): hard constraints H1-H6 and soft warnings S1-S8.

Every finding's `detail` holds only JSON-serialisable values and never a person's name.
Diners appear as their `diner_key`.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from minemydinner.models import Rating, Recipe
from minemydinner.planning.composition import (
    CATEGORY_OF_DISH_TYPE,
    COUNTED_DISH_TYPES,
    Bound,
    count_categories,
    outside,
    required_counts,
    side_ranges,
    sum_exact_amounts,
)
from minemydinner.planning.context import PlanningContext
from minemydinner.planning.dietary import DietaryCheck, RuleConflict, check_recipes
from minemydinner.planning.named import recipes_using
from minemydinner.planning.stock import compute_missing, missing_cookware

EXPIRY_SOON_DAYS = 3
LOW_RATING = 2
TOTAL_TIME_FACTOR = 1.5
MAX_TOTAL_COUNT = 12


class PlanSpec(BaseModel):
    mode: Literal["fridge_only", "shopping"]
    dishes: int = Field(ge=0)
    soups: int = Field(ge=0)
    time_limit_min: int | None = None
    # A split of `dishes` (meat plus vegetable); None means that side is not named. Each
    # named side has a bound (None reads as exact); side_ranges turns them into ranges.
    meat_dishes: int | None = Field(default=None, ge=0)
    vegetable_dishes: int | None = Field(default=None, ge=0)
    meat_bound: Bound | None = None
    vegetable_bound: Bound | None = None
    staples: int = Field(default=0, ge=0)
    desserts: int = Field(default=0, ge=0)
    snacks: int = Field(default=0, ge=0)
    # Named ingredients, copied from the parsed request (design 5.1).
    must_use: list[str] = Field(default_factory=list)
    all_use: list[str] = Field(default_factory=list)
    prefer_use: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _read_old_split(cls, data: Any) -> Any:
        """A spec saved before bounds existed carries `split_is_minimum`: true means both
        named sides are "at least", false means exact (design 5.11, old records)."""
        if not isinstance(data, dict) or "split_is_minimum" not in data:
            return data
        data = dict(data)
        minimum = bool(data.pop("split_is_minimum"))
        for side, bound in (("meat_dishes", "meat_bound"), ("vegetable_dishes", "vegetable_bound")):
            if data.get(side) is not None and data.get(bound) is None:
                data[bound] = "at_least" if minimum else "exact"
        return data

    @model_validator(mode="after")
    def _total_in_range(self) -> "PlanSpec":
        total = self.dishes + self.soups + self.staples + self.desserts + self.snacks
        if not 1 <= total <= MAX_TOTAL_COUNT:
            raise ValueError(f"A plan needs between 1 and {MAX_TOTAL_COUNT} dishes in total")
        return self


class H2Override(BaseModel):
    """A confirmed acceptance of one H2 failure. A rule-conflict override names the exact
    target (allergen, ingredient_id, or text). `unknown_allergens` accepts only the
    unknown-allergen block of the recipe and never covers a rule conflict."""

    recipe_id: int
    diner_key: str
    allergen: str | None = None
    ingredient_id: int | None = None
    text: str | None = None
    unknown_allergens: bool = False


class Finding(BaseModel):
    code: str
    recipe_id: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    violations: list[Finding] = Field(default_factory=list)
    warnings: list[Finding] = Field(default_factory=list)
    passed: bool = True


def _h5(session: Session, recipe_ids: list[int]) -> tuple[list[Finding], list[int]]:
    """Duplicates and unknown ids. Returns the findings and the distinct valid ids."""
    existing = set(session.scalars(select(Recipe.id).where(Recipe.id.in_(set(recipe_ids)))))
    counts = Counter(recipe_ids)
    findings = []
    for recipe_id in counts:
        if recipe_id not in existing:
            findings.append(Finding(code="H5", recipe_id=recipe_id, detail={"reason": "not_found"}))
        if counts[recipe_id] > 1:
            findings.append(Finding(code="H5", recipe_id=recipe_id, detail={"reason": "duplicate"}))
    return findings, [recipe_id for recipe_id in counts if recipe_id in existing]


def _h1(session: Session, ctx: PlanningContext, ids: list[int]) -> list[Finding]:
    missing = compute_missing(session, ctx, ids)
    return [
        Finding(code="H1", recipe_id=recipe_id, detail={"missing": [m.name for m in lines]})
        for recipe_id, lines in missing.items()
        if lines
    ]


def _covered(conflict: RuleConflict, overrides: Sequence[H2Override]) -> bool:
    return any(
        o.recipe_id == conflict.recipe_id
        and o.diner_key == conflict.diner_key
        and not o.unknown_allergens
        and o.allergen == conflict.allergen
        and o.ingredient_id == conflict.ingredient_id
        and o.text == conflict.text
        for o in overrides
    )


def _unknown_covered(recipe_id: int, overrides: Sequence[H2Override]) -> bool:
    return any(
        o.unknown_allergens
        and o.recipe_id == recipe_id
        and o.allergen is None
        and o.ingredient_id is None
        and o.text is None
        for o in overrides
    )


def _h2(
    check: DietaryCheck, ids: list[int], overrides: Sequence[H2Override]
) -> list[Finding]:
    findings = [
        Finding(
            code="H2",
            recipe_id=c.recipe_id,
            detail={
                "diner_key": c.diner_key,
                "rule_type": c.rule_type,
                "allergen": c.allergen,
                "ingredient_id": c.ingredient_id,
                "text": c.text,
                "line": c.line_name,
            },
        )
        for c in check.hard
        if not _covered(c, overrides)
    ]
    for recipe_id in ids:
        if check.blocked_by_unknown(recipe_id) and not _unknown_covered(recipe_id, overrides):
            findings.append(
                Finding(
                    code="H2",
                    recipe_id=recipe_id,
                    detail={"unknown": list(check.unknown_allergen_lines[recipe_id])},
                )
            )
    return findings


def _h3(session: Session, ctx: PlanningContext, ids: list[int]) -> list[Finding]:
    return [
        Finding(
            code="H3",
            recipe_id=recipe_id,
            detail={"missing": [name for _, name in items], "cookware_ids": [i for i, _ in items]},
        )
        for recipe_id, items in missing_cookware(session, ctx, ids).items()
        if items
    ]


def _h4(spec: PlanSpec, recipes: list[Recipe]) -> list[Finding]:
    """Every exact count matches, and the meat and vegetable dishes are each within their
    range (design 5.11). The detail carries both ranges."""
    present = count_categories(r.dish_type for r in recipes)
    required = required_counts(spec)
    ranges = side_ranges(spec)
    if all(present[c] == need for c, need in required.items()) and all(
        outside(present[side], low, high) == 0 for side, (low, high) in ranges.items()
    ):
        return []
    return [
        Finding(
            code="H4",
            detail={
                "required": required,
                "present": present,
                "ranges": {side: [low, high] for side, (low, high) in ranges.items()},
            },
        )
    ]


def _h7(
    session: Session, ctx: PlanningContext, spec: PlanSpec, recipes: list[Recipe]
) -> list[Finding]:
    """Named ingredients (design 5.1): `must_use` needs one counted dish that uses the name,
    `all_use` needs every counted dish to. Recipes of type "other" are ignored."""
    counted = [r.id for r in recipes if r.dish_type in CATEGORY_OF_DISH_TYPE]
    if not counted:
        return []
    findings = []
    for rule, names in (("must", spec.must_use), ("all", spec.all_use)):
        for name in names:
            if not name.strip():
                continue
            using = recipes_using(session, ctx, name, counted)
            without = [i for i in counted if i not in using]
            if (rule == "must" and not using) or (rule == "all" and without):
                findings.append(
                    Finding(
                        code="H7",
                        detail={"name": name, "rule": rule, "recipes_without": without},
                    )
                )
    return findings


def _h6(spec: PlanSpec, recipes: list[Recipe]) -> list[Finding]:
    limit = spec.time_limit_min
    if limit is None:
        return []
    return [
        Finding(
            code="H6",
            recipe_id=r.id,
            detail={"time_min": r.time_min, "limit": limit, "time_source": r.time_source},
        )
        for r in recipes
        if r.time_source in ("stated", "user") and r.time_min is not None and r.time_min > limit
    ]


def _s1(ctx: PlanningContext, recipes: list[Recipe]) -> list[Finding]:
    return [
        Finding(
            code="S1",
            detail={
                "ingredient_id": need.ingredient_id,
                "name": need.name,
                "needed": need.needed,
                "have": need.have,
                "unit": need.unit,
                "parts": need.parts,
            },
        )
        for need in sum_exact_amounts(ctx, recipes).values()
        if need.needed > need.have
    ]


def _s2(check: DietaryCheck) -> list[Finding]:
    return [
        Finding(
            code="S2",
            recipe_id=c.recipe_id,
            detail={"diner_key": c.diner_key, "line": c.line_name},
        )
        for c in check.dislikes
    ]


def _ancestors(ctx: PlanningContext, ingredient_id: int) -> set[int]:
    seen = {ingredient_id}
    current = ctx.parents.get(ingredient_id)
    while current is not None and current not in seen:
        seen.add(current)
        current = ctx.parents.get(current)
    return seen


def _s3(ctx: PlanningContext, recipes: list[Recipe], today: date) -> list[Finding]:
    used = {line.ingredient_id for r in recipes for line in r.ingredients if line.ingredient_id}
    cutoff = today + timedelta(days=EXPIRY_SOON_DAYS)
    findings = []
    for ingredient_id, item in sorted(ctx.stock.items()):
        if item.expires_on is None or item.expires_on > cutoff:
            continue
        # A selected line needing this item or one of its ancestors is met by the item.
        if _ancestors(ctx, ingredient_id) & used:
            continue
        findings.append(
            Finding(
                code="S3",
                detail={"ingredient_id": ingredient_id, "expires_on": item.expires_on.isoformat()},
            )
        )
    return findings


def _s4(session: Session, recipes: list[Recipe]) -> list[Finding]:
    scores = dict(
        session.execute(
            select(Rating.recipe_id, Rating.score).where(Rating.recipe_id.in_([r.id for r in recipes]))
        ).all()
    )
    return [
        Finding(code="S4", recipe_id=r.id, detail={"score": scores[r.id]})
        for r in recipes
        if scores.get(r.id) is not None and scores[r.id] <= LOW_RATING
    ]


def _s5(recipes: list[Recipe]) -> list[Finding]:
    types = [r.dish_type for r in recipes if r.dish_type in COUNTED_DISH_TYPES]
    if len(types) >= 2 and len(set(types)) == 1:
        return [Finding(code="S5", detail={"dish_type": types[0], "count": len(types)})]
    return []


def _s6(ctx: PlanningContext, recipes: list[Recipe]) -> list[Finding]:
    findings = []
    for recipe in recipes:
        for line in recipe.ingredients:
            item = ctx.stock.get(line.ingredient_id) if line.ingredient_id else None
            if line.amount_type == "vague" and not line.optional and item and item.level == "low":
                findings.append(
                    Finding(
                        code="S6",
                        recipe_id=recipe.id,
                        detail={"ingredient_id": line.ingredient_id, "name": line.name},
                    )
                )
    return findings


def _s7(spec: PlanSpec, recipes: list[Recipe]) -> list[Finding]:
    limit = spec.time_limit_min
    if limit is None:
        return []
    findings = []
    for r in recipes:
        if r.time_min is None:
            findings.append(Finding(code="S7", recipe_id=r.id, detail={"kind": "unknown"}))
        elif r.time_source == "estimated":
            findings.append(
                Finding(
                    code="S7", recipe_id=r.id, detail={"kind": "estimated", "time_min": r.time_min}
                )
            )
    total = sum(r.time_min for r in recipes if r.time_min is not None)
    if total > TOTAL_TIME_FACTOR * limit:
        findings.append(
            Finding(code="S7", detail={"kind": "total", "total_min": total, "limit": limit})
        )
    return findings


def _s8(ctx: PlanningContext, check: DietaryCheck, ids: list[int]) -> list[Finding]:
    findings = []
    if ctx.constraints.unconfirmed_diners > 0:
        findings.append(
            Finding(code="S8", detail={"unconfirmed_diners": ctx.constraints.unconfirmed_diners})
        )
    for recipe_id in ids:
        lines = check.unrecognized_lines.get(recipe_id)
        if lines:
            findings.append(Finding(code="S8", recipe_id=recipe_id, detail={"unrecognized": lines}))
    return findings


def validate_plan(
    session: Session,
    ctx: PlanningContext,
    spec: PlanSpec,
    recipe_ids: list[int],
    overrides: Sequence[H2Override] = (),
) -> ValidationResult:
    """Check a plan against H1-H7 and S1-S8. Opens no session and writes nothing."""
    violations, ids = _h5(session, recipe_ids)
    recipes = list(
        session.scalars(
            select(Recipe)
            .where(Recipe.id.in_(ids))
            .options(selectinload(Recipe.ingredients))
        )
    )
    order = {recipe_id: position for position, recipe_id in enumerate(ids)}
    recipes.sort(key=lambda r: order[r.id])
    check = check_recipes(session, ctx.rules, ids)

    if spec.mode == "fridge_only":
        violations += _h1(session, ctx, ids)
    violations += _h2(check, ids, overrides)
    violations += _h3(session, ctx, ids)
    violations += _h4(spec, recipes)
    violations += _h7(session, ctx, spec, recipes)
    violations += _h6(spec, recipes)

    warnings = (
        _s1(ctx, recipes)
        + _s2(check)
        + _s3(ctx, recipes, date.today())
        + _s4(session, recipes)
        + _s5(recipes)
        + _s6(ctx, recipes)
        + _s7(spec, recipes)
        + _s8(ctx, check, ids)
    )
    return ValidationResult(violations=violations, warnings=warnings, passed=not violations)
