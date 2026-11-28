"""The change path between the frame and the reply (plan 3b-2 spec 5.4-5.6). Pure code: the
graph's nodes `build_change`, `pick_dishes` and `present_change` call these.

- A button turn has no reading: `button_reading` turns the offering turn's unfinished parts
  (kept on its card, `UNFINISHED`) into one, so a button that lifts a condition re-tries what
  that condition blocked (spec 5.5).
- After the picker, `slot_results` pairs every slot with its dish, `unfinished_items` lists
  what could not be done, `change_actions` and `change_unmet` give the card's next actions
  and unmet entries for those parts only, and `unfinished_reading` keeps them for a button.
- With no usable picker answer, `fallback_pick` makes the frame's drop choices in code (a
  stated count is hard) and fills no slot.
- When the picker says the wish is not met, `without_unmet_wish_picks` leaves a slot made only
  for the wish unfilled, so its dish stays (user decision, 2026-11-23)."""

from collections.abc import Collection

from pydantic import ValidationError

from minemydinner.agent.actions import on_plan_counts
from minemydinner.agent.card_text import NEXT_ACTION_ORDER, next_actions
from minemydinner.agent.follow_up import FollowUpReading, ReadAdd, ReadConditions, ReadCounts
from minemydinner.agent.frame import Frame, fold
from minemydinner.agent.picker import ChoiceAnswer, Picks, SlotPick, choice_slots
from minemydinner.agent.change_text import (
    over_limit_entries,
    wish_unmet_actions,
    wish_unmet_entries,
)
from minemydinner.planning.composition import (
    CATEGORY_OF_DISH_TYPE,
    DISH_TYPE_OF_CATEGORY,
    SPLIT_CATEGORIES,
    count_categories,
)
from minemydinner.planning.validate import PlanSpec

# The card field (server only) holding a change's unfinished parts: {"reading": the parts as a
# FollowUpReading dump, "words": the user's words of that turn}. A button pressed on the card
# re-runs them against the plan it shows.
UNFINISHED = "unfinished"
# The spec fields a change compares to tell a change of conditions with the same dishes.
CONDITION_FIELDS = ("mode", "time_limit_min", "must_use", "prefer_use", "all_use")
_COUNTS = tuple(ReadCounts.model_fields)
# The dish types of the meat and vegetable sides.
SIDE_TYPES = tuple(DISH_TYPE_OF_CATEGORY[c] for c in SPLIT_CATEGORIES)
# The reason kept on a slot left unfilled because its dish did not meet the wish.
WISH_NOT_MET = "does not meet the wish"


def needs_picker(frame: Frame) -> bool:
    """The picker is called only when a dish must be chosen or a choice made (spec 5.4)."""
    return bool(frame.slots or frame.choices)


def side_adds_left(unfinished: dict | None, request: dict) -> int:
    """How many meat or vegetable dishes the unfinished typed adds of a card still ask for
    (`request`, the card's request). When the card's turn stated counts, only the adds on a
    side it stated count (a stated total already holds the others); when it stated none, every
    typed meat or vegetable add counts, as the plan shown does not hold it yet."""
    if request.get("counts_stated_now"):
        sides = {c for c in SPLIT_CATEGORIES if request.get(c) is not None}
    else:
        sides = set(SPLIT_CATEGORIES)
    data = (unfinished or {}).get("reading") or {}
    return sum(
        add.get("count") or 0
        for add in data.get("add") or []
        if CATEGORY_OF_DISH_TYPE.get(add.get("dish_type") or "") in sides
        and not (add.get("name") or "").strip()
    )


def any_mix_request(request: dict, plan: dict, card: dict | None) -> dict:
    """The request "any mix" starts from on a card that revises, for the relax_split button
    and a typed `any_mix` alike (plan 3b-2 (two) Task 8): a request whose turn stated no count
    is rebased on the plan shown (`on_plan_counts`), and its total grows by the meat or
    vegetable adds the newest card could not make (`side_adds_left`). A request that stated
    counts keeps them."""
    if request.get("counts_stated_now"):
        return request
    base = on_plan_counts(request, plan)
    if card is not None and base.get("dishes") is not None:
        left = side_adds_left(card.get(UNFINISHED), card.get("request") or {})
        base = {**base, "dishes": base["dishes"] + left}
    return base


def relax_split_spec(card: dict) -> dict | None:
    """The spec relax_split ("any mix") works from on a card: the card's own, with the total
    raised by the meat or vegetable dishes the card's change could not add (`side_adds_left`),
    so the button keeps the total that turn asked for. A spec without a total counts the
    card's dishes. (A turn that stated no count rebases its request on the plan shown, whose
    total wins; `route_intent` raises that total by the same adds.)"""
    spec = card.get("spec")
    left = side_adds_left(card.get(UNFINISHED), card.get("request") or {})
    if not left or spec is None:
        return spec
    shown = spec.get("dishes")
    if shown is None:
        shown = count_categories(r.get("dish_type", "") for r in card.get("recipes") or [])[
            "dishes"
        ]
    return {**spec, "dishes": shown + left}


def button_reading(
    unfinished: dict | None, request: dict, spec: PlanSpec, *, any_side: bool = False
) -> FollowUpReading:
    """The reading a button turn feeds the frame: the offering turn's unfinished parts, and
    the counts of the button's request when it states them (`counts_stated_now`: a count
    button, or an offering turn that stated counts), as `build_frame` binds them with no
    reading. Without a statement the change list decides the counts, so an unfinished add
    still grows the plan. `any_side` (relax_split): an unfinished add of a meat or vegetable
    dish becomes an add of any type, so the picker may take either side."""
    data = dict((unfinished or {}).get("reading") or {})
    adds = list(data.get("add") or [])
    if any_side:
        adds = [
            {**add, "dish_type": None} if add.get("dish_type") in SIDE_TYPES else add
            for add in adds
        ]
    counts = {}
    if request.get("counts_stated_now"):
        counts = {
            name: getattr(spec, name) for name in _COUNTS if getattr(spec, name) is not None
        }
    return FollowUpReading(
        kind="change",
        remove=data.get("remove") or [],
        add=adds,
        wish=data.get("wish") or "",
        wish_targets=data.get("wish_targets") or [],
        counts=ReadCounts(**counts),
    )


def fallback_pick(frame: Frame) -> tuple[Frame, Picks]:
    """The frame and answer applied when the picker gave no usable answer: the drop choices
    (a stated lower count) take their first candidates, the replace choices are left out
    (nothing changes for a wish), and no slot is filled, so a dish that breaks a rule still
    leaves and every other replaced dish stays (spec 5.5). When the count also needed an
    added dish, its drops are withheld (`withhold_count_drops`)."""
    drops = [c for c in frame.choices if c.mode == "drop"]
    reduced = frame.model_copy(update={"choices": drops})
    answer = Picks(
        choices=[ChoiceAnswer(index=i, ids=c.candidates[: c.up_to]) for i, c in enumerate(drops)],
        slots=[
            SlotPick(index=i, recipe_id=None, reason="no answer")
            for i in range(len(frame.slots))
        ],
    )
    return withhold_count_drops(reduced, answer)


def count_add_unfilled(frame: Frame, answer: Picks) -> bool:
    """An add slot made to meet a stated count is left without a dish."""
    filled = {s.index for s in answer.slots if s.recipe_id is not None}
    return any(s.for_count and i not in filled for i, s in enumerate(frame.slots))


def withhold_count_drops(frame: Frame, answer: Picks) -> tuple[Frame, Picks]:
    """When an add slot made for a stated count stays unfilled, the count's drops are not
    applied either (spec 5.5: the plan never shrinks unasked): a drop of the other side,
    made to keep the total, would remove a dish the user never named and come no closer to
    the count. Drops the user asked for ("去掉汤") are not count drops and stay. Returns the
    frame and answer unchanged otherwise."""
    if not count_add_unfilled(frame, answer):
        return frame, answer
    gone = set(frame.count_drops)
    kept = [i for i, c in enumerate(frame.choices) if c.mode != "drop"]
    new_index = {old: new for new, old in enumerate(kept)}
    reduced = frame.model_copy(
        update={
            "drops": [i for i in frame.drops if i not in gone],
            "count_drops": [],
            "choices": [frame.choices[i] for i in kept],
        }
    )
    choices = [
        c.model_copy(update={"index": new_index[c.index]})
        for c in answer.choices
        if c.index in new_index
    ]
    return reduced, answer.model_copy(update={"choices": choices})


def without_picks(
    answer: Picks, recipe_ids: Collection[int], reason: str = "breaks a rule"
) -> Picks:
    """The answer with the slots that chose one of `recipe_ids` left unfilled, with `reason`
    (kept in the records)."""
    bad = set(recipe_ids)
    slots = [
        s.model_copy(update={"recipe_id": None, "reason": reason})
        if s.recipe_id in bad
        else s
        for s in answer.slots
    ]
    return answer.model_copy(update={"slots": slots})


def wish_only_slots(frame: Frame) -> set[int]:
    """The frame's slots whose one reason is the wish: a wish target, or an add or a replace
    the reading marked `for_wish`; never a dish that breaks a rule, a dish the user named, or
    a slot for a must-use ingredient or a stated count."""
    return {
        index
        for index, slot in enumerate(frame.slots)
        if slot.for_wish
        and slot.reason == "asked"
        and not slot.named
        and not slot.must_use
        and not slot.for_count
    }


def needs_wish_met(frame: Frame, answer: Picks) -> bool:
    """A slot made only for the wish (`wish_only_slots`) got a dish, so the wish-only guard
    (`without_unmet_wish_picks`) needs to know whether the wish is met. A salvaged answer,
    whose wish_met is unknown, is then set aside as no answer (Task 11 fix 3): applying it
    could place a dish that misses the wish, and calling it unmet could say something false."""
    only = wish_only_slots(frame)
    return any(s.index in only and s.recipe_id is not None for s in answer.slots)


def reading_changes_nothing(reading: FollowUpReading, meal_avoid: Collection[str]) -> bool:
    """A `change` reading with a wish that changes nothing at all: no removal, no add, no wish
    target, no stated count, no change of conditions, and this meal's avoidance the same as
    `meal_avoid` (the plan's). A structural check, not a reading of the words (Task 11 fix 3):
    the reader is then asked once more to check its reading, not told to correct it, since a
    wish with empty targets can be a correct reading (less of a food no dish has,
    follow_up_v9). A reading with no wish is never one (the ambiguous reading that leads to
    the which-dish question, Task 7 ruling 12)."""
    return (
        reading.kind == "change"
        and bool(reading.wish.strip())
        and not reading.remove
        and not reading.add
        and not reading.wish_targets
        and all(v is None for v in reading.counts.model_dump().values())
        and reading.conditions == ReadConditions()
        and sorted(reading.meal_avoid) == sorted(meal_avoid)
    )


def reader_problem(reading: FollowUpReading) -> str:
    """The problem a reading that changes nothing is asked again with, in plain words. It is
    neutral (follow_up_v9): it names the three readings a wish can have and pushes toward none,
    since a wish that touches no dish of the plan (less of a food no dish has, or a complaint
    about one) rightly changes nothing, while a wish for something the plan lacks adds or
    replaces a dish (follow_up_v9's own line)."""
    said = (
        f'Your reading says "{reading.reading.strip()}"'
        if reading.reading.strip()
        else f'Your reading has the wish "{reading.wish.strip()}"'
    )
    return (
        f"{said}, but it changes no dish. Check which this wish is: a wish for something the "
        "plan lacks adds a dish or replaces one for it (for_wish); a wish about dishes of the "
        "plan lists them in wish_targets; only less of a food no dish has, or a complaint "
        "about one, changes no dish, so answer the same reading again. Answer with the whole "
        "reading."
    )


def without_unmet_wish_picks(frame: Frame, answer: Picks) -> tuple[Picks, set[int]]:
    """When the picker says the wish is not met (`wish_met` false), the slots made only for
    the wish (`wish_only_slots`) are left unfilled: a replaced dish stays and an add adds
    nothing (user decisions, 2026-11-23: a dish changed or added for a wish must meet it).
    Every other slot keeps its dish.
    Returns the answer and the slot numbers emptied."""
    if answer.wish_met is not False:
        return answer, set()
    only = wish_only_slots(frame)
    emptied = {s.index for s in answer.slots if s.index in only and s.recipe_id is not None}
    if not emptied:
        return answer, set()
    slots = [
        s.model_copy(update={"recipe_id": None, "reason": WISH_NOT_MET})
        if s.index in emptied
        else s
        for s in answer.slots
    ]
    return answer.model_copy(update={"slots": slots}), emptied


def slot_results(frame: Frame, answer: Picks) -> list[dict]:
    """Every slot (the frame's, then those the replace choices made) with the dish that fills
    it (`recipe_id`, None when nothing fits) and the picker's reason."""
    answers: dict[int, SlotPick] = {}
    for item in answer.slots:
        answers.setdefault(item.index, item)
    results = []
    for index, slot in enumerate(choice_slots(frame, answer)):
        item = answers.get(index)
        results.append(
            {
                **slot.model_dump(),
                "recipe_id": item.recipe_id if item else None,
                "why": item.reason if item else "",
            }
        )
    return results


def unfinished_items(
    frame: Frame,
    slots: list[dict],
    titles: dict[int, str],
    breaking: dict[int, str],
    must_missed: Collection[str] | None = None,
    not_done: bool = False,
) -> list[dict]:
    """What a change could not do, in order: named dishes not placed ("missing": an exact
    title hidden by a hard condition, then a named slot nothing filled: "not in the library"
    only when the picker itself answered null (`picker_null`), the real reason when the
    picker's dish was hidden (`hidden`), else "not_done": the picker gave no usable answer,
    so nothing is said about the library), then the
    slots nothing filled: a dish that breaks a rule is removed ("removed"), any other keeps
    its dish or adds nothing ("unfilled"; `must_use` names the must-use ingredient the slot
    was for), then a must-use choice that made no slot. `why` is why a replaced dish had to
    change (a `breaking` reason), so the next action fits it.

    `must_missed`: the must-use names the result still misses (validation's H7); None reads
    as every name. Only those are said unused, and a must-use choice's empty slot whose
    ingredient another new dish uses is not unfinished at all: the choice was only for it.

    `not_done` (and a slot's own `not_done`): the picker gave no usable answer (code's
    fallback), so an unfilled item says it could not be done this time, never that nothing
    fits (Task 11 fix 3)."""
    missed = None if must_missed is None else {fold(n) for n in must_missed}

    def unused(names: list[str]) -> list[str]:
        return [n for n in names if missed is None or fold(n) in missed]

    items: list[dict] = []
    for missing in frame.missing:
        detail = missing.detail
        items.append(
            {
                "kind": "missing",
                "name": missing.name,
                "reason": missing.reason,
                "recipe_id": detail.get("recipe_id"),
                "title": detail.get("title"),
                "blocked": detail.get("blocked"),
                "time_min": detail.get("time_min"),
                "replaces": detail.get("replaces"),
            }
        )
    for slot in slots:
        if slot["recipe_id"] is not None or not slot.get("named"):
            continue
        # A named slot nothing filled: the dish the user named is not placed. Its replaced
        # dish stays, or leaves when it breaks a rule (said as "removed" by the caller).
        hidden = slot.get("hidden") or {}
        blocked = hidden.get("blocked")
        if blocked is None:
            reason = "not_in_library" if slot.get("picker_null") else "not_done"
        else:
            reason = "over_time_limit" if blocked == "time_limit" else "breaks_rule"
        items.append(
            {
                "kind": "missing",
                "name": slot["named"],
                "reason": reason,
                "recipe_id": hidden.get("recipe_id"),
                "title": hidden.get("title"),
                "blocked": blocked,
                "time_min": hidden.get("time_min"),
                "replaces": slot["replaces"],
            }
        )
    for slot in slots:
        if slot["recipe_id"] is not None or slot.get("named"):
            continue
        if slot.get("wish_unmet"):
            # Left unfilled because its dish did not meet the wish: the unmet-wish sentence
            # says it, and the dish stays.
            continue
        replaces = slot["replaces"]
        must = list(slot.get("must_use") or [])
        if must and slot["kind"] == "replace" and not unused(must):
            continue
        why = breaking.get(replaces, "") if replaces is not None else ""
        if replaces is not None and slot["reason"] == "breaks_rule":
            items.append(
                {
                    "kind": "removed",
                    "recipe_id": replaces,
                    "title": titles.get(replaces, ""),
                    "why": why,
                    # No usable picker answer: no replacement was chosen, none was ruled out.
                    "not_done": bool(slot.get("not_done")),
                }
            )
            continue
        items.append(
            {
                "kind": "unfilled",
                "recipe_id": replaces,
                "dish_type": slot["dish_type"] or "",
                "title": titles.get(replaces) if replaces is not None else None,
                "why": why,
                # An add for a stated count: the reply says the count could not be met.
                "for_count": bool(slot.get("for_count")),
                # A must-use slot: the reply says the ingredient is not used.
                "must_use": unused(must),
                # No usable picker answer: nothing was tried for this slot.
                "not_done": bool(slot.get("not_done")),
            }
        )
    # A must-use choice that made no slot at all (no usable picker answer, `fallback_pick`):
    # its ingredient is not used either.
    named = {fold(n) for slot in slots for n in slot.get("must_use") or []}
    for choice in frame.choices:
        left = unused([n for n in choice.must_use if fold(n) not in named])
        if left:
            items.append(
                {
                    "kind": "unfilled",
                    "recipe_id": None,
                    "dish_type": "",
                    "title": None,
                    "why": "",
                    "for_count": False,
                    "must_use": left,
                    "not_done": not_done,
                }
            )
    return items


def unused_must(items: list[dict]) -> set[str]:
    """The must-use names (folded) the unfinished parts leave unused."""
    return {fold(n) for item in items for n in item.get("must_use") or []}


def change_actions(items: list[dict], spec: dict, wish_unmet: bool) -> list[str]:
    """The next actions for the unfinished parts only (spec 5.5), as today's failure reasons
    map them: a dish not in the library offers importing; a dish hidden by the time limit,
    stock or cookware offers dropping the limit, shopping or registering cookware; a slot
    nothing fits (and an unmet wish) offers what an unmet wish does; an added meat or
    vegetable dish nothing fits (a typed add left unfilled, whether or not the turn stated a
    count) also offers relax_split, as a short side on a new plan."""
    fridge = spec["mode"] == "fridge_only"
    chosen: set[str] = set()

    def for_block(block: str | None) -> bool:
        if block == "time_limit":
            chosen.add("drop_time_limit")
        elif block == "stock":
            if fridge:
                chosen.add("switch_to_shopping")
        elif block == "cookware":
            chosen.add("register_cookware")
        else:
            return False
        return True

    for item in items:
        if item["kind"] == "missing":
            if item["reason"] == "not_in_library":
                chosen.add("import_recipes")
            elif item["reason"] == "over_time_limit":
                chosen.add("drop_time_limit")
            elif item["reason"] == "breaks_rule":
                for_block(item.get("blocked"))
        elif item["kind"] == "unfilled" and not for_block(item.get("why")):
            chosen.update(wish_unmet_actions(spec))
            category = CATEGORY_OF_DISH_TYPE.get(item.get("dish_type") or "")
            side_add = item.get("recipe_id") is None and category in SPLIT_CATEGORIES
            if side_add:
                chosen.update(next_actions(spec, [], [], short_categories=[category]))
    if wish_unmet:
        chosen.update(wish_unmet_actions(spec))
    return [a for a in NEXT_ACTION_ORDER if a in chosen]


def change_unmet(
    items: list[dict], sentences: list[str], limit: int | None, locale: str, wish_unmet: bool
) -> list[dict]:
    """The card's unmet entries for the unfinished parts, each with its reply sentence; a
    dish hidden by the time limit is worded as an H6 finding (how long it takes)."""
    entries: list[dict] = []
    for item, text in zip(items, sentences, strict=True):
        if item["kind"] == "missing" and item["reason"] == "over_time_limit" and limit is not None:
            dish = {"id": item["recipe_id"], "title": item["title"], "time_min": item["time_min"]}
            entries += over_limit_entries([dish], limit, locale)
            continue
        code = item["reason"] if item["kind"] == "missing" else item["kind"]
        entries.append({"code": code, "recipe_id": item.get("recipe_id"), "text": text})
    if wish_unmet:
        entries += wish_unmet_entries(locale)
    return entries


def unfinished_reading(
    reading: FollowUpReading,
    frame: Frame,
    slots: list[dict],
    wish_met: bool | None,
    result_ids: list[int],
) -> dict | None:
    """The parts of a reading a button should re-run (as a FollowUpReading dump), or None: the
    removals whose dish is still in the result (a named replacement not placed, a slot
    nothing filled), the named adds not placed, the typed adds no dish filled, and the wish's
    targets still to change: a target nothing replaced, and when the wish was not met the
    dish that took a target's place. A removal code refused (a locked dish without a
    quote) is not re-run."""
    refused = {item.get("id") for item in frame.ignored if "id" in item}
    left = set(result_ids)
    remove = [
        r.model_dump() for r in reading.remove if r.id in left and r.id not in refused
    ]
    add = [ReadAdd(name=m.name).model_dump() for m in frame.missing if m.detail.get("replaces") is None]
    unfilled_adds: dict[tuple, int] = {}
    for slot in slots:
        if slot["kind"] != "add" or slot["recipe_id"] is not None:
            continue
        if slot.get("named"):
            add.append(ReadAdd(name=slot["named"]).model_dump())
        else:
            # An add for the wish stays one, so a button keeps the wish's guard on it.
            key = (slot["dish_type"], bool(slot.get("for_wish")))
            unfilled_adds[key] = unfilled_adds.get(key, 0) + 1
    add += [
        ReadAdd(dish_type=t, count=n, for_wish=w).model_dump()
        for (t, w), n in unfilled_adds.items()
    ]
    wished = [
        s
        for i, s in enumerate(slots)
        if s.get("for_wish") or (i >= len(frame.slots) and not s.get("must_use"))
    ]
    targets: list[int] = []
    if reading.wish.strip():
        for slot in wished:
            if slot["recipe_id"] is None:
                targets.append(slot["replaces"])
            elif wish_met is False:
                targets.append(slot["recipe_id"])
    targets = [i for i in dict.fromkeys(targets) if i in left]
    if not (remove or add or targets):
        return None
    return {
        "remove": remove,
        "add": add,
        "wish": reading.wish,
        "wish_targets": targets,
    }


def result_spec(spec: PlanSpec, dish_types: list[str]) -> PlanSpec:
    """The spec with the counts of the dishes it now has, sides exact, as a revision's own
    counts follow its change list; the spec itself when they cannot form one."""
    counts = count_categories(dish_types)
    updates = {name: counts[name] for name in _COUNTS}
    try:
        return PlanSpec.model_validate(
            {**spec.model_dump(), **updates, "meat_bound": "exact", "vegetable_bound": "exact"}
        )
    except ValidationError:
        return spec


def conditions_changed(plan: dict, spec: dict, meal_avoid: list[str]) -> bool:
    """The change states conditions the plan does not carry yet (a mode, a time limit, named
    ingredients, this meal's avoidance): the same dishes then make a new version."""
    old = plan.get("spec") or {}
    if any((old.get(f) or None) != (spec.get(f) or None) for f in CONDITION_FIELDS):
        return True
    return sorted(plan.get("meal_avoid") or []) != sorted(meal_avoid)
