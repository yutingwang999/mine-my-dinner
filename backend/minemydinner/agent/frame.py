"""The frame of a change (plan 3b-2 spec 5.4): pure code decides, from a follow-up reading (or a
button's request) and the current plan, which dishes keep, drop or become slots to fill, and
which bounded choices the picker may make. The old revise step's after-the-fact rules become
this code: a locked dish goes only with a quote that points at it, or because it breaks a rule
(R2, R3); a replacement has the replaced dish's type unless the user named it or the counts say
otherwise (R7); a general wish changes at most half of the unlocked dishes (R4); a change that
leaves no dish is flagged (R9).

`build_frame` needs no database: the caller passes the dish facts, the dishes that break the new
request (`breaking_from` over `validate_plan`) and a lookup for named dishes (`lookup_named`)."""

from collections.abc import Callable, Iterable
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.agent.follow_up import FollowUpReading
from minemydinner.agent.revision import DishFacts, max_general_changes, quote_matches
from minemydinner.models import Recipe
from minemydinner.planning.composition import (
    CATEGORY_OF_DISH_TYPE,
    DISH_TYPE_OF_CATEGORY,
    count_categories,
    ranges_for,
)
from minemydinner.planning.context import MEAL_KEY, PlanningContext
from minemydinner.planning.dietary import check_recipes
from minemydinner.planning.search import FIRM_TIME_SOURCES
from minemydinner.planning.stock import compute_missing, missing_cookware
from minemydinner.planning.validate import PlanSpec, ValidationResult

# Why a plan dish breaks the new request (the values of `breaking`), and why a found named dish
# is hidden (`NamedLookup.blocked`). A dish that breaks a diner rule or a one-meal avoidance is
# removed when no replacement is found (spec 5.5); the others keep their dish.
RULE_BREAKS = ("diner_rule", "meal_avoid")
BREAK_REASONS = (*RULE_BREAKS, "time_limit", "cookware", "stock")
_BREAK_OF_CODE = {"H1": "stock", "H3": "cookware", "H6": "time_limit"}
# A named dish's title may differ from the name by one character only when the name is at least
# this long: "鱼汤" must never become "鸡汤".
NEAR_MATCH_MIN_LENGTH = 4
_EXACT_CATEGORIES = ("soups", "staples", "desserts", "snacks")


class Slot(BaseModel):
    """A new dish the picker must choose: in place of `replaces`, or added."""

    kind: Literal["replace", "add"]
    dish_type: str
    replaces: int | None = None
    # "breaks_rule": the replaced dish breaks a diner rule or a one-meal avoidance, so it is
    # removed even when no dish fills the slot (spec 5.5). Every other slot is "asked".
    reason: Literal["asked", "breaks_rule"] = "asked"
    # True for an add slot made to meet a stated count: when it stays unfilled, the count
    # drops made with it are withheld, so the plan never shrinks unasked (spec 5.5).
    for_count: bool = False


class Choice(BaseModel):
    """Which unlocked dishes go, chosen by the picker inside a limit code set. `mode` "replace":
    up to `up_to` of `candidates` are replaced by dishes of their own type (a general wish, a
    must-use ingredient). `mode` "drop": exactly `up_to` of `candidates` are dropped (a lower
    count). `dish_type` None: the candidates are of more than one type."""

    dish_type: str | None
    candidates: list[int]
    up_to: int
    mode: Literal["drop", "replace"]


class Missing(BaseModel):
    """A named dish that is not placed. `detail` holds `replaces` (the plan dish it was to
    replace, or None for an add) and, for a found dish, `recipe_id`, `title`, `blocked` and
    `time_min`."""

    name: str
    reason: Literal["not_in_library", "ambiguous", "over_time_limit", "breaks_rule"]
    detail: dict = Field(default_factory=dict)


class NamedLookup(BaseModel):
    """A named dish looked up in the library. `blocked` names the hard condition that hides a
    found dish (one of `BREAK_REASONS`), or None when it can be placed."""

    status: Literal["found", "missing", "ambiguous"]
    recipe_id: int | None
    title: str
    blocked: str | None
    dish_type: str = ""
    time_min: int | None = None
    # The found dish's line names and their dictionary names, for the must-use check.
    ingredient_names: list[str] = Field(default_factory=list)


class Frame(BaseModel):
    """What a change does to the plan. `keep`: plan dishes code keeps (choice candidates among
    them). `drops`: plan dishes that leave with no slot (named drops, a lower count, a dish a
    placed named dish replaces). `slots`: new dishes to choose. `placed`: named library dishes
    added directly. `missing`: named dishes not placed. `ignored`: parts of the reading code
    refused, with the reason, for the record."""

    keep: list[int]
    drops: list[int]
    slots: list[Slot]
    choices: list[Choice]
    placed: list[int]
    missing: list[Missing]
    empty_plan: bool
    ignored: list[dict]
    # The drops made to meet a stated count (among `drops`); every drop choice is one too.
    count_drops: list[int] = Field(default_factory=list)


def _break_rank(reason: str) -> int:
    return BREAK_REASONS.index(reason)


def breaking_from(result: ValidationResult) -> dict[int, str]:
    """The dishes `validate_plan` found breaking the request, each with its reason: a diner
    rule or an unknown allergen (H2), a one-meal avoidance (H2 under MEAL_KEY), stock in
    fridge-only mode (H1), cookware (H3), the time limit (H6). A rule reason wins over the
    others, because it decides what happens to an unfilled slot."""
    found: dict[int, str] = {}
    for finding in result.violations:
        if finding.recipe_id is None:
            continue
        if finding.code == "H2":
            reason = "meal_avoid" if finding.detail.get("diner_key") == MEAL_KEY else "diner_rule"
        elif finding.code in _BREAK_OF_CODE:
            reason = _BREAK_OF_CODE[finding.code]
        else:
            continue
        current = found.get(finding.recipe_id)
        if current is None or _break_rank(reason) < _break_rank(current):
            found[finding.recipe_id] = reason
    return found


def fold(text: str) -> str:
    """A name compared case- and space-insensitively."""
    return "".join(text.casefold().split())


_fold = fold


def _one_off(title: str, name: str) -> bool:
    """Same length and exactly one character differs ("宫爆鸡丁" for "宫保鸡丁")."""
    if len(title) != len(name) or len(name) < NEAR_MATCH_MIN_LENGTH:
        return False
    return sum(a != b for a, b in zip(title, name)) == 1


def _blocked(session: Session, recipe: Recipe, request: dict, ctx: PlanningContext) -> str | None:
    """The first hard condition that hides the recipe: a diner rule or one-meal avoidance, the
    time limit (the rule of `over_time_limit` and H6: a stated or user-given time over it),
    cookware, or stock in fridge-only mode."""
    check = check_recipes(session, ctx.rules, [recipe.id])
    if check.hard or check.blocked_by_unknown(recipe.id):
        keys = {c.diner_key for c in check.hard}
        return "meal_avoid" if keys == {MEAL_KEY} else "diner_rule"
    limit = request.get("time_limit_min")
    if (
        limit is not None
        and recipe.time_source in FIRM_TIME_SOURCES
        and recipe.time_min is not None
        and recipe.time_min > limit
    ):
        return "time_limit"
    if missing_cookware(session, ctx, [recipe.id]).get(recipe.id):
        return "cookware"
    if (request.get("mode") or "fridge_only") == "fridge_only" and compute_missing(
        session, ctx, [recipe.id]
    ).get(recipe.id):
        return "stock"
    return None


def lookup_named(
    session: Session, name: str, request: dict, *, ctx: PlanningContext
) -> NamedLookup:
    """A dish the user named, in the library: the exact title (case and spaces ignored), else a
    title one character off the name (unique title only; two titles make it ambiguous). A
    found dish carries the hard condition that hides it (`_blocked`). `request` gives the new
    request's `mode` and `time_limit_min` (a spec's dump works too; no mode reads as
    fridge-only, the default); `ctx` must hold the new request's diner and meal rules."""
    key = _fold(name)
    if not key:
        return NamedLookup(status="missing", recipe_id=None, title=name, blocked=None)
    rows = session.execute(select(Recipe.id, Recipe.title).order_by(Recipe.id)).all()
    matches = [(i, t) for i, t in rows if _fold(t) == key]
    if not matches:
        matches = [(i, t) for i, t in rows if _one_off(_fold(t), key)]
        if len({_fold(t) for _, t in matches}) > 1:
            return NamedLookup(status="ambiguous", recipe_id=None, title=name, blocked=None)
    if not matches:
        return NamedLookup(status="missing", recipe_id=None, title=name, blocked=None)
    recipe = session.get(Recipe, matches[0][0])
    return NamedLookup(
        status="found",
        recipe_id=recipe.id,
        title=recipe.title,
        blocked=_blocked(session, recipe, request, ctx),
        dish_type=recipe.dish_type,
        time_min=recipe.time_min,
        ingredient_names=_line_names(recipe),
    )


def _line_names(recipe: Recipe) -> list[str]:
    """Every line's name and its dictionary names, as `dish_facts` gathers them."""
    names: list[str] = []
    for line in recipe.ingredients:
        names.append(line.name)
        if line.ingredient is not None:
            names += [n for n in (line.ingredient.name_zh, line.ingredient.name_en) if n]
    return list(dict.fromkeys(names))


def _names_use(names: Iterable[str], name: str) -> bool:
    key = _fold(name)
    return any(key in _fold(n) for n in names if n.strip())


def _uses(facts: DishFacts | None, name: str) -> bool:
    return facts is not None and _names_use(facts.ingredient_names, name)


class _Builder:
    """The working state of one frame; `build_frame` drives it step by step."""

    def __init__(self, plan_ids: list[int], locked: set[int], facts: dict[int, DishFacts]):
        self.plan_ids = list(dict.fromkeys(plan_ids))
        self.locked = locked
        self.facts = facts
        self.handled: set[int] = set()  # plan dishes that drop or have a slot
        self.drops: list[int] = []
        self.slots: list[Slot] = []
        self.choices: list[Choice] = []
        self.placed: list[int] = []
        self.placed_types: list[str] = []
        self.placed_names: list[list[str]] = []
        self.missing: list[Missing] = []
        self.ignored: list[dict] = []
        self.count_drops: list[int] = []

    def type_of(self, dish_id: int) -> str:
        facts = self.facts.get(dish_id)
        return facts.dish_type if facts else ""

    def kept(self) -> list[int]:
        return [i for i in self.plan_ids if i not in self.handled]

    def drop(self, dish_id: int) -> None:
        self.handled.add(dish_id)
        self.drops.append(dish_id)

    def replace(self, dish_id: int, reason: str) -> None:
        self.handled.add(dish_id)
        self.slots.append(
            Slot(
                kind="replace",
                dish_type=self.type_of(dish_id),
                replaces=dish_id,
                reason="breaks_rule" if reason in RULE_BREAKS else "asked",
            )
        )

    def place(self, name: str, lookup: NamedLookup, replaces: int | None) -> bool:
        """Place a named dish when it is found and usable; else record why not."""
        detail: dict = {"replaces": replaces}
        if lookup.status != "found" or lookup.recipe_id is None:
            reason = "ambiguous" if lookup.status == "ambiguous" else "not_in_library"
            self.missing.append(Missing(name=name, reason=reason, detail=detail))
            return False
        if lookup.recipe_id in self.plan_ids or lookup.recipe_id in self.placed:
            self.ignored.append(
                {"name": name, "recipe_id": lookup.recipe_id, "reason": "already_in_plan"}
            )
            return False
        if lookup.blocked is not None:
            detail.update(
                recipe_id=lookup.recipe_id,
                title=lookup.title,
                blocked=lookup.blocked,
                time_min=lookup.time_min,
            )
            reason = "over_time_limit" if lookup.blocked == "time_limit" else "breaks_rule"
            self.missing.append(Missing(name=name, reason=reason, detail=detail))
            return False
        self.placed.append(lookup.recipe_id)
        self.placed_types.append(lookup.dish_type)
        self.placed_names.append(list(lookup.ingredient_names))
        return True

    def candidates(self, dish_types: Iterable[str]) -> list[int]:
        """Kept unlocked dishes of these types: what a choice may touch."""
        allowed = set(dish_types)
        return [i for i in self.kept() if i not in self.locked and self.type_of(i) in allowed]

    def result_types(self) -> list[str]:
        """The dish types of the plan after the decided changes (choices not applied)."""
        return [
            *(self.type_of(i) for i in self.kept()),
            *(s.dish_type for s in self.slots),
            *self.placed_types,
        ]

    def remove_count(self, dish_types: tuple[str, ...], count: int) -> None:
        """Take `count` dishes of these types out to meet a stated count: first new dishes not
        yet chosen (add slots, then replace slots, whose dish then simply drops), then a drop
        of unlocked dishes; code drops them itself when there is nothing to choose."""
        for kind in ("add", "replace"):
            for slot in [s for s in self.slots if s.kind == kind and s.dish_type in dish_types]:
                if count == 0:
                    return
                self.slots.remove(slot)
                if slot.replaces is not None:
                    self.drops.append(slot.replaces)
                    # A dish that breaks a diner rule or a one-meal avoidance goes for good
                    # (spec 5.5); only an asked replacement may be withheld with the count.
                    if slot.reason != "breaks_rule":
                        self.count_drops.append(slot.replaces)
                count -= 1
        if count <= 0:
            return
        pool = self.candidates(dish_types)
        if len(pool) <= count:
            for dish_id in pool:
                self.drop(dish_id)
                self.count_drops.append(dish_id)
            return
        dish_type = dish_types[0] if len(dish_types) == 1 else None
        self.choices.append(Choice(dish_type=dish_type, candidates=pool, up_to=count, mode="drop"))

    def add_count(self, dish_type: str, count: int, for_count: bool = False) -> None:
        self.slots.extend(
            Slot(kind="add", dish_type=dish_type, for_count=for_count) for _ in range(count)
        )


def _side_changes(
    present: dict[str, int], total: int, ranges: dict[str, tuple[int, int]]
) -> tuple[int, int, int, int, int]:
    """How the meat and vegetable sides change to meet a total and side ranges:
    (meat drops, vegetable drops, drops of either side, meat adds, vegetable adds). A drop
    either side may take is left to the picker; an add either side may take goes to the side
    with fewer dishes (vegetable on a tie)."""
    (m_low, m_high), (v_low, v_high) = ranges["meat_dishes"], ranges["vegetable_dishes"]
    meat, veg = present["meat_dishes"], present["vegetable_dishes"]
    m = min(max(meat, m_low), max(m_high, 0))
    v = min(max(veg, v_low), max(v_high, 0))
    either = 0
    gap = m + v - total
    if gap > 0:
        can_m, can_v = max(0, m - m_low), max(0, v - v_low)
        forced_m, forced_v = max(0, gap - can_v), max(0, gap - can_m)
        m, v = m - min(forced_m, m), v - min(forced_v, v)
        either = max(0, gap - forced_m - forced_v)
    elif gap < 0:
        for _ in range(-gap):
            room_m, room_v = m < m_high, v < v_high
            if room_m and (not room_v or m < v):
                m += 1
            else:
                v += 1
    return (
        max(0, meat - m),
        max(0, veg - v),
        either,
        max(0, m - meat),
        max(0, v - veg),
    )


def _meet_counts(builder: _Builder, spec: PlanSpec, stated: set[str] | None) -> None:
    """Make the plan's counts the spec's: drops and adds per category (spec 5.4, "the counts").
    Only the `stated` count fields bind (None: all of them, on a button); an unstated category
    keeps what the change list gives. An unstated total stays the change list's, and an
    unstated side is free."""
    present = count_categories(builder.result_types())
    binds = (lambda name: True) if stated is None else (lambda name: name in stated)
    for category in _EXACT_CATEGORIES:
        if not binds(category):
            continue
        dish_type = DISH_TYPE_OF_CATEGORY[category]
        gap = present[category] - getattr(spec, category)
        if gap > 0:
            builder.remove_count((dish_type,), gap)
        elif gap < 0:
            builder.add_count(dish_type, -gap, for_count=True)
    meat_type = DISH_TYPE_OF_CATEGORY["meat_dishes"]
    veg_type = DISH_TYPE_OF_CATEGORY["vegetable_dishes"]
    sides = [s for s in ("meat_dishes", "vegetable_dishes") if binds(s)]
    if not binds("dishes") and not sides:
        return
    total = spec.dishes if binds("dishes") else present["dishes"]
    named = {
        side: (getattr(spec, side), getattr(spec, side.split("_")[0] + "_bound"))
        if side in sides
        else (None, None)
        for side in ("meat_dishes", "vegetable_dishes")
    }
    ranges = ranges_for(total, *named["meat_dishes"], *named["vegetable_dishes"])
    drop_m, drop_v, either, add_m, add_v = _side_changes(present, total, ranges)
    if drop_m:
        builder.remove_count((meat_type,), drop_m)
    if drop_v:
        builder.remove_count((veg_type,), drop_v)
    if either:
        builder.remove_count((meat_type, veg_type), either)
    builder.add_count(meat_type, add_m, for_count=True)
    builder.add_count(veg_type, add_v, for_count=True)


def _locked_quote_ok(facts: DishFacts | None, quote: str, message: str) -> bool:
    """Today's quote rule for a locked dish (R1, R2): the quote is in the message and names
    the dish by its title, an ingredient or its category word."""
    if facts is None or not quote.strip() or quote not in message:
        return False
    return quote_matches(quote, facts.title, facts.ingredient_names, facts.category_words)


def build_frame(
    plan_ids: list[int],
    locked: set[int],
    facts: dict[int, DishFacts],
    spec_before: dict,
    spec_after: PlanSpec,
    reading: FollowUpReading | None,
    breaking: dict[int, str],
    lookup: Callable[[str], NamedLookup],
    message: str,
    *,
    uncovered_must: list[str] | None = None,
) -> Frame:
    """The frame of a change (spec 5.4). `reading` is None on a button turn: the frame then
    comes from the specs alone. `spec_before` is the plan's spec (its `must_use` tells which
    must-use names are new); `spec_after` the new request's spec. `breaking` maps a plan dish
    to why it breaks the new request (`breaking_from`). `uncovered_must`, when given, is the
    must-use names no dish covers by validation's own rule; otherwise code matches the new
    must-use names against the dishes' ingredient names.

    Order: named removals (a locked dish only with a quote that points at it), dishes that
    break the request (same-type replace slots; a locked dish only for a rule or avoidance), named and typed adds, the
    counts (only the fields the message states, or all of them on a button), a general wish, an uncovered
    must-use ingredient, and last whether any dish is left."""
    builder = _Builder(plan_ids, locked, facts)
    in_plan = set(builder.plan_ids)
    # A locked dish goes for a rule or an avoidance only (today's rule); stock, cookware or
    # the time limit never move it, as `split_violations` allows them for locked dishes.
    breaking = {
        i: reason for i, reason in breaking.items() if i not in locked or reason in RULE_BREAKS
    }

    # Named removals.
    for item in reading.remove if reading else []:
        if item.id not in in_plan:
            builder.ignored.append({"id": item.id, "reason": "not_in_plan"})
            continue
        if item.id in builder.handled:
            builder.ignored.append({"id": item.id, "reason": "duplicate"})
            continue
        if (
            item.id in locked
            and item.id not in breaking
            and not _locked_quote_ok(facts.get(item.id), item.quote, message)
        ):
            builder.ignored.append(
                {"id": item.id, "reason": "locked_without_quote", "quote": item.quote}
            )
            continue
        if item.mode == "drop":
            builder.drop(item.id)
        elif item.with_name.strip():
            if builder.place(item.with_name, lookup(item.with_name), item.id):
                builder.drop(item.id)
        else:
            builder.replace(item.id, breaking.get(item.id, ""))

    # Dishes that break the new request; a dish already handled keeps its one entry.
    for dish_id in builder.plan_ids:
        if dish_id in breaking and dish_id not in builder.handled:
            builder.replace(dish_id, breaking[dish_id])

    # Adds: a named dish is placed or missing; a typed add is a slot per dish.
    for add in reading.add if reading else []:
        if add.name.strip():
            builder.place(add.name, lookup(add.name), None)
        elif add.dish_type not in CATEGORY_OF_DISH_TYPE:
            builder.ignored.append({"add": add.model_dump(), "reason": "unknown_dish_type"})
        elif add.count < 1:
            builder.ignored.append({"add": add.model_dump(), "reason": "bad_count"})
        else:
            builder.add_count(add.dish_type, add.count)

    # The counts: from the change list, unless the message (or a button) states them.
    if reading is None:
        _meet_counts(builder, spec_after, None)
    else:
        stated = {k for k, v in reading.counts.model_dump().items() if v is not None}
        if stated:
            _meet_counts(builder, spec_after, stated)

    # A general wish: the picker may replace up to half of the unlocked dishes.
    if reading is not None and reading.wish.strip() and reading.wish_general:
        unlocked = len([i for i in builder.plan_ids if i not in locked])
        pool = builder.candidates(CATEGORY_OF_DISH_TYPE)
        up_to = min(max_general_changes(unlocked), len(pool))
        if up_to:
            builder.choices.append(
                Choice(dish_type=None, candidates=pool, up_to=up_to, mode="replace")
            )

    # A must-use ingredient no dish uses: one replace, unless an added dish can use it.
    if uncovered_must is None:
        before = {_fold(n) for n in spec_before.get("must_use") or []}
        uncovered_must = [
            name
            for name in spec_after.must_use
            if name.strip()
            and _fold(name) not in before
            and not any(_uses(facts.get(i), name) for i in builder.kept())
            and not any(_names_use(names, name) for names in builder.placed_names)
        ]
    if uncovered_must and not any(s.kind == "add" for s in builder.slots):
        pool = builder.candidates(CATEGORY_OF_DISH_TYPE)
        for _ in uncovered_must:
            if pool:
                builder.choices.append(
                    Choice(dish_type=None, candidates=pool, up_to=1, mode="replace")
                )

    dropped_by_choice = sum(c.up_to for c in builder.choices if c.mode == "drop")
    left = len(builder.kept()) - dropped_by_choice + len(builder.slots) + len(builder.placed)
    return Frame(
        keep=builder.kept(),
        drops=builder.drops,
        slots=builder.slots,
        choices=builder.choices,
        placed=builder.placed,
        missing=builder.missing,
        empty_plan=left <= 0,
        ignored=builder.ignored,
        count_drops=builder.count_drops,
    )
