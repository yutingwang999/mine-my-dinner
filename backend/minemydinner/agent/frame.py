"""The frame of a change (plan 3b-2 spec 5.4): pure code decides, from a follow-up reading (or a
button's request) and the current plan, which dishes keep, drop or become slots to fill, and
which bounded choices the picker may make.

Code checks, it does not interpret (spec 5.4, amended 2026-11-23): whatever needs reading
language is a model's judgment, and code holds only the invariants. A locked dish goes only
with a quote that is the user's own words from this message, or because it breaks a rule; a
replace slot carries no dish type (the picker keeps the kind unless the user's words or the
wish point elsewhere; stated counts are checked on the result); the dishes a wish touches are the reader's `wish_targets`, capped at
half of the unlocked dishes; a named dish is placed by code only when it equals a library
title, and otherwise the picker finds the dish the words mean; a change that leaves no dish
is flagged.

`build_frame` needs no database: the caller passes the dish facts, the dishes that break the new
request (`breaking_from` over `validate_plan`) and a lookup for named dishes (`lookup_named`)."""

from collections.abc import Callable, Iterable
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.agent.change_rules import DishFacts, max_general_changes
from minemydinner.agent.follow_up import FollowUpReading
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
_EXACT_CATEGORIES = ("soups", "staples", "desserts", "snacks")


class Slot(BaseModel):
    """A new dish the picker must choose: in place of `replaces`, or added. A replace slot has
    no dish type (the picker keeps the kind unless the words or the wish point elsewhere); an add
    keeps the type the reading gave it, or none for a named dish or an untyped add (a meat or
    vegetable dish, either kind, checked on the picker's answer)."""

    kind: Literal["replace", "add"]
    dish_type: str | None = None
    replaces: int | None = None
    # "breaks_rule": the replaced dish breaks a diner rule or a one-meal avoidance, so it is
    # removed even when no dish fills the slot (spec 5.5). Every other slot is "asked".
    reason: Literal["asked", "breaks_rule"] = "asked"
    # True for an add slot made to meet a stated count: when it stays unfilled, the count
    # drops made with it are withheld, so the plan never shrinks unasked (spec 5.5).
    for_count: bool = False
    # The must-use ingredients the new dish is for: a slot made by a must-use choice.
    must_use: list[str] = Field(default_factory=list)
    # The user's words that point at the replaced dish (the reading's quote).
    words: str = ""
    # A dish the user named that is no library title, in the user's words: the picker finds
    # the library dish the words mean, or answers that nothing fits ("not in the library").
    named: str = ""
    # Only for the wish: a dish the wish touches (the reading's `wish_targets`), or an add or
    # a replace the reading marked `for_wish`. Left unfilled when the wish is not met.
    for_wish: bool = False


class Choice(BaseModel):
    """Which unlocked dishes go, chosen by the picker inside a limit code set. `mode` "replace":
    up to `up_to` of `candidates` are replaced, each by a new slot in its place (a must-use
    ingredient, or an old kept card's wish). `mode` "drop": exactly `up_to` of `candidates` are
    dropped (a lower count). `dish_type` None: the candidates are of more than one type."""

    dish_type: str | None
    candidates: list[int]
    up_to: int
    mode: Literal["drop", "replace"]
    # The must-use ingredient no dish uses that this replace choice is for (spec 5.4): the new
    # dish must use it. Empty for every other choice.
    must_use: list[str] = Field(default_factory=list)


class Missing(BaseModel):
    """A named dish that is not placed. `detail` holds `replaces` (the plan dish it was to
    replace, or None for an add) and, for a found dish, `recipe_id`, `title`, `blocked` and
    `time_min`."""

    name: str
    reason: Literal["not_in_library", "over_time_limit", "breaks_rule"]
    detail: dict = Field(default_factory=dict)


class NamedLookup(BaseModel):
    """A named dish looked up in the library. `blocked` names the hard condition that hides a
    found dish (one of `BREAK_REASONS`), or None when it can be placed."""

    status: Literal["found", "missing"]
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
    refused, with the reason, for the record; code reads the typed fields below instead."""

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
    # The counts this message (or a button) states, as [low, high] per count field ("dishes",
    # "meat_dishes", "soups", ...): invariants code checks the picker's result against.
    count_ranges: dict[str, list[int]] = Field(default_factory=dict)
    # The wish targets left out, in the reader's order (also in `ignored`, as the record):
    # dishes the user added or kept by hand, and dishes over the cap on a wish's changes.
    locked_wish_targets: list[int] = Field(default_factory=list)
    capped_wish_targets: list[int] = Field(default_factory=list)


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


def hidden_by(session: Session, recipe: Recipe, request: dict, ctx: PlanningContext) -> str | None:
    """The first hard condition that hides the recipe: a diner rule or one-meal avoidance, the
    time limit (the rule of `over_time_limit` and H6: a stated or user-given time over it),
    cookware, or stock in fridge-only mode. Also used on a dish the picker found for a named
    slot, so a dish the user named gets the real reason, never "not in the library"."""
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
    """A dish the user named, in the library: the exact title only (case and spaces ignored).
    Any other name is the picker's to judge (spec 5.4): code never guesses a near title. A
    found dish carries the hard condition that hides it (`hidden_by`). `request` gives the new
    request's `mode` and `time_limit_min` (a spec's dump works too; no mode reads as
    fridge-only, the default); `ctx` must hold the new request's diner and meal rules."""
    key = _fold(name)
    if not key:
        return NamedLookup(status="missing", recipe_id=None, title=name, blocked=None)
    rows = session.execute(select(Recipe.id, Recipe.title).order_by(Recipe.id)).all()
    matches = [(i, t) for i, t in rows if _fold(t) == key]
    if not matches:
        return NamedLookup(status="missing", recipe_id=None, title=name, blocked=None)
    recipe = session.get(Recipe, matches[0][0])
    return NamedLookup(
        status="found",
        recipe_id=recipe.id,
        title=recipe.title,
        blocked=hidden_by(session, recipe, request, ctx),
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
        self.count_ranges: dict[str, list[int]] = {}

    def type_of(self, dish_id: int) -> str:
        facts = self.facts.get(dish_id)
        return facts.dish_type if facts else ""

    def planned_type(self, slot: Slot) -> str:
        """The type a slot counts as while code meets the counts: an add's own type, a replace
        slot the replaced dish's (a replace keeps the plan's size and, until the picker
        chooses, its balance). The picker may choose another kind; stated counts are then
        checked on its result (`picker.apply_picks`)."""
        if slot.dish_type:
            return slot.dish_type
        return self.type_of(slot.replaces) if slot.replaces is not None else ""

    def kept(self) -> list[int]:
        return [i for i in self.plan_ids if i not in self.handled]

    def drop(self, dish_id: int) -> None:
        self.handled.add(dish_id)
        self.drops.append(dish_id)

    def replace(
        self, dish_id: int, reason: str, *, words: str = "", named: str = "", for_wish: bool = False
    ) -> None:
        self.handled.add(dish_id)
        self.slots.append(
            Slot(
                kind="replace",
                replaces=dish_id,
                reason="breaks_rule" if reason in RULE_BREAKS else "asked",
                words=words,
                named=named,
                for_wish=for_wish,
            )
        )

    def place(self, name: str, lookup: NamedLookup, replaces: int | None) -> str:
        """Place a named dish that equals a library title and is usable ("placed"). A name that
        is no title is "not_found": the caller makes a named slot for the picker. A title
        already in the plan, or hidden by a hard condition, is recorded and "refused"."""
        detail: dict = {"replaces": replaces}
        if lookup.status != "found" or lookup.recipe_id is None:
            return "not_found"
        if lookup.recipe_id in self.plan_ids or lookup.recipe_id in self.placed:
            self.ignored.append(
                {"name": name, "recipe_id": lookup.recipe_id, "reason": "already_in_plan"}
            )
            return "refused"
        if lookup.blocked is not None:
            detail.update(
                recipe_id=lookup.recipe_id,
                title=lookup.title,
                blocked=lookup.blocked,
                time_min=lookup.time_min,
            )
            reason = "over_time_limit" if lookup.blocked == "time_limit" else "breaks_rule"
            self.missing.append(Missing(name=name, reason=reason, detail=detail))
            return "refused"
        self.placed.append(lookup.recipe_id)
        self.placed_types.append(lookup.dish_type)
        self.placed_names.append(list(lookup.ingredient_names))
        return "placed"

    def candidates(self, dish_types: Iterable[str]) -> list[int]:
        """Kept unlocked dishes of these types: what a choice may touch."""
        allowed = set(dish_types)
        return [i for i in self.kept() if i not in self.locked and self.type_of(i) in allowed]

    def result_types(self) -> list[str]:
        """The dish types of the plan after the decided changes (choices not applied)."""
        return [
            *(self.type_of(i) for i in self.kept()),
            *(self.planned_type(s) for s in self.slots),
            *self.placed_types,
        ]

    def remove_count(self, dish_types: tuple[str, ...], count: int) -> None:
        """Take `count` dishes of these types out to meet a stated count: first new dishes not
        yet chosen (add slots, then replace slots, whose dish then simply drops), then a drop
        of unlocked dishes; code drops them itself when there is nothing to choose. A named
        slot is never taken: the user asked for that dish."""
        for kind in ("add", "replace"):
            for slot in [
                s
                for s in self.slots
                if s.kind == kind and not s.named and self.planned_type(s) in dish_types
            ]:
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

    def untyped_adds(self) -> int:
        """Untyped adds (no type, no name): each is one more dish, a meat or vegetable dish of
        either kind (checked on the picker's answer)."""
        return sum(1 for s in self.slots if s.kind == "add" and not s.dish_type and not s.named)

    def add_count(
        self, dish_type: str | None, count: int, for_count: bool = False, for_wish: bool = False
    ) -> None:
        self.slots.extend(
            Slot(kind="add", dish_type=dish_type, for_count=for_count, for_wish=for_wish)
            for _ in range(count)
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
        builder.count_ranges[category] = [getattr(spec, category)] * 2
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
    # An add of any type is one of the dishes already, on whichever side the picker puts it:
    # the sides only make up the rest of the total.
    sides_total = max(total - builder.untyped_adds(), 0) if binds("dishes") else total
    named = {
        side: (getattr(spec, side), getattr(spec, side.split("_")[0] + "_bound"))
        if side in sides
        else (None, None)
        for side in ("meat_dishes", "vegetable_dishes")
    }
    ranges = ranges_for(total, *named["meat_dishes"], *named["vegetable_dishes"])
    if binds("dishes"):
        builder.count_ranges["dishes"] = [total, total]
    for side in sides:
        builder.count_ranges[side] = list(ranges[side])
    drop_m, drop_v, either, add_m, add_v = _side_changes(present, sides_total, ranges)
    if drop_m:
        builder.remove_count((meat_type,), drop_m)
    if drop_v:
        builder.remove_count((veg_type,), drop_v)
    if either:
        builder.remove_count((meat_type, veg_type), either)
    builder.add_count(meat_type, add_m, for_count=True)
    builder.add_count(veg_type, add_v, for_count=True)


def _locked_quote_ok(quote: str, message: str) -> bool:
    """A locked dish the reading removes (spec 5.4): the quote must be the user's own words
    from this message. Whether the words point at the dish is the reader's judgment, not a
    string match against the dish's title or ingredients."""
    return bool(quote.strip()) and quote in message


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
    remake: bool = False,
) -> Frame:
    """The frame of a change (spec 5.4). `reading` is None on a button turn: the frame then
    comes from the specs alone. `spec_before` is the plan's spec (its `must_use` tells which
    must-use names are new); `spec_after` the new request's spec. `breaking` maps a plan dish
    to why it breaks the new request (`breaking_from`). `uncovered_must`, when given, is the
    must-use names no dish covers by validation's own rule; otherwise code matches the new
    must-use names against the dishes' ingredient names.

    Order: named removals (a locked dish only with a quote from this message), dishes that
    break the request (replace slots; a locked dish only for a rule or avoidance), the wish's
    targets (at most half of the unlocked dishes), named and typed adds, the counts (only the
    fields the message states, or all of them on a button), an uncovered must-use
    ingredient, and last whether any dish is left. `remake` turns every unlocked dish not yet
    handled into a replace slot, after the rule-breaking step."""
    builder = _Builder(plan_ids, locked, facts)
    in_plan = set(builder.plan_ids)
    # A locked dish goes for a rule or an avoidance only (today's rule); stock, cookware or
    # the time limit never move it, as a manual version keeps them as warnings
    # (edits.ALLOWED_CODES).
    breaking = {
        i: reason for i, reason in breaking.items() if i not in locked or reason in RULE_BREAKS
    }

    # Named removals. A dish replaced only to meet the wish (`for_wish`) counts toward the
    # wish's cap below; such a removal is itself never capped.
    wish_removals: list[int] = []
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
            and not _locked_quote_ok(item.quote, message)
        ):
            builder.ignored.append(
                {"id": item.id, "reason": "locked_without_quote", "quote": item.quote}
            )
            continue
        if item.mode == "drop":
            builder.drop(item.id)
            continue
        name = item.with_name.strip()
        placed = builder.place(name, lookup(name), item.id) if name else "not_found"
        if placed == "placed":
            builder.drop(item.id)
        elif placed == "not_found":
            # No name, or a name that is no library title: the picker chooses (a named slot
            # carries the user's words for it to find).
            builder.replace(
                item.id,
                breaking.get(item.id, ""),
                words=item.quote,
                named=name,
                for_wish=item.for_wish,
            )
        if item.for_wish and item.id not in locked and placed in ("placed", "not_found"):
            # A dish that leaves for the wish (a name hidden by a condition keeps the dish).
            wish_removals.append(item.id)

    # Dishes that break the new request; a dish already handled keeps its one entry.
    for dish_id in builder.plan_ids:
        if dish_id in breaking and dish_id not in builder.handled:
            builder.replace(dish_id, breaking[dish_id])

    # Make it again (who eats changed so much that most of the plan must go): every unlocked
    # dish not yet handled becomes a replace slot. A locked dish that breaks a rule is a slot
    # already; a locked dish that breaks none stays.
    if remake:
        for dish_id in builder.plan_ids:
            if dish_id not in builder.handled and dish_id not in locked:
                builder.replace(dish_id, "")

    # The dishes the wish touches, as the reader names them: unlocked dishes only, at most
    # half of them (rounded up), the reader's order deciding which stay. The reading's
    # removals for the wish (`wish_removals`) are among them, so they count first. A dish
    # already changing needs no slot of its own; the wish guides its replacement too. The
    # targets left out are kept by reason (locked, over the cap) for the reply.
    locked_targets: list[int] = []
    capped_targets: list[int] = []
    if reading is not None and reading.wish.strip():
        limit = max_general_changes(len([i for i in builder.plan_ids if i not in locked]))
        taken = len(wish_removals)
        for dish_id in dict.fromkeys(reading.wish_targets):
            if dish_id not in in_plan:
                builder.ignored.append({"wish_target": dish_id, "reason": "not_in_plan"})
            elif dish_id in locked:
                builder.ignored.append({"wish_target": dish_id, "reason": "locked"})
                locked_targets.append(dish_id)
            elif dish_id in builder.handled:
                continue
            elif taken >= limit:
                builder.ignored.append({"wish_target": dish_id, "reason": "over_limit"})
                capped_targets.append(dish_id)
            else:
                builder.replace(dish_id, "", for_wish=True)
                taken += 1

    # Adds: a library title is placed; any other name is a named slot; a typed add is a slot
    # per dish.
    for add in reading.add if reading else []:
        if add.name.strip():
            if builder.place(add.name, lookup(add.name), None) == "not_found":
                builder.slots.append(Slot(kind="add", named=add.name.strip()))
        elif add.dish_type is not None and add.dish_type not in CATEGORY_OF_DISH_TYPE:
            builder.ignored.append({"add": add.model_dump(), "reason": "unknown_dish_type"})
        elif add.count < 1:
            builder.ignored.append({"add": add.model_dump(), "reason": "bad_count"})
        else:
            # No type: a meat or vegetable dish, either kind (spec 5.4: code sets no type the
            # reading did not give; the picker's answer is checked).
            builder.add_count(add.dish_type, add.count, for_wish=add.for_wish)

    # The counts: from the change list, unless the message (or a button) states them.
    if reading is None:
        _meet_counts(builder, spec_after, None)
    else:
        stated = {k for k, v in reading.counts.model_dump().items() if v is not None}
        if stated:
            _meet_counts(builder, spec_after, stated)

    # A must-use ingredient no dish uses: one replace, unless an added dish takes its place.
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
    adds = [s for s in builder.slots if s.kind == "add"]
    if uncovered_must and adds:
        # The add takes the ingredient's place (spec 5.4): the first add made for a stated
        # count, else the first add, must use every uncovered name.
        carrier = next((s for s in adds if s.for_count), adds[0])
        carrier.must_use = list(uncovered_must)
    elif uncovered_must:
        pool = builder.candidates(CATEGORY_OF_DISH_TYPE)
        for name in uncovered_must:
            if pool:
                builder.choices.append(
                    Choice(
                        dish_type=None, candidates=pool, up_to=1, mode="replace", must_use=[name]
                    )
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
        count_ranges=builder.count_ranges,
        locked_wish_targets=locked_targets,
        capped_wish_targets=capped_targets,
    )
