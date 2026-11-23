"""The change path between the frame and the reply (plan 3b-2 spec 5.4-5.6). Pure code: the
graph's nodes `build_change`, `pick_dishes` and `present_change` call these.

- A button turn has no reading: `button_reading` turns the offering turn's unfinished parts
  (kept on its card, `UNFINISHED`) into one, so a button that lifts a condition re-tries what
  that condition blocked (spec 5.5).
- After the picker, `slot_results` pairs every slot with its dish, `unfinished_items` lists
  what could not be done, `change_actions` and `change_unmet` give the card's next actions
  and unmet entries for those parts only, and `unfinished_reading` keeps them for a button.
- With no usable picker answer, `fallback_pick` makes the frame's drop choices in code (a
  stated count is hard) and fills no slot."""

from collections.abc import Collection

from pydantic import ValidationError

from minemydinner.agent.card_text import NEXT_ACTION_ORDER
from minemydinner.agent.follow_up import FollowUpReading, ReadAdd, ReadCounts
from minemydinner.agent.frame import Frame
from minemydinner.agent.picker import ChoiceAnswer, PickerAnswer, SlotAnswer, choice_slots
from minemydinner.agent.revise_step import (
    over_limit_entries,
    wish_unmet_actions,
    wish_unmet_entries,
)
from minemydinner.agent.revision import DishFacts
from minemydinner.planning.composition import count_categories
from minemydinner.planning.validate import PlanSpec

# The card field (server only) holding a change's unfinished parts: {"reading": the parts as a
# FollowUpReading dump, "words": the user's words of that turn}. A button pressed on the card
# re-runs them against the plan it shows.
UNFINISHED = "unfinished"
# The spec fields a change compares to tell a change of conditions with the same dishes.
CONDITION_FIELDS = ("mode", "time_limit_min", "must_use", "prefer_use", "all_use")
_COUNTS = tuple(ReadCounts.model_fields)


def needs_picker(frame: Frame) -> bool:
    """The picker is called only when a dish must be chosen or a choice made (spec 5.4)."""
    return bool(frame.slots or frame.choices)


def button_reading(unfinished: dict | None, request: dict, spec: PlanSpec) -> FollowUpReading:
    """The reading a button turn feeds the frame: the offering turn's unfinished parts, and
    the counts of the button's request when it states them (`counts_stated_now`: a count
    button, or an offering turn that stated counts), as `build_frame` binds them with no
    reading. Without a statement the change list decides the counts, so an unfinished add
    still grows the plan."""
    data = dict((unfinished or {}).get("reading") or {})
    counts = {}
    if request.get("counts_stated_now"):
        counts = {
            name: getattr(spec, name) for name in _COUNTS if getattr(spec, name) is not None
        }
    return FollowUpReading(
        kind="change",
        remove=data.get("remove") or [],
        add=data.get("add") or [],
        wish=data.get("wish") or "",
        wish_general=bool(data.get("wish_general")),
        counts=ReadCounts(**counts),
    )


def fallback_pick(frame: Frame) -> tuple[Frame, PickerAnswer]:
    """The frame and answer applied when the picker gave no usable answer: the drop choices
    (a stated lower count) take their first candidates, the replace choices are left out
    (nothing changes for a wish), and no slot is filled, so a dish that breaks a rule still
    leaves and every other replaced dish stays (spec 5.5). When the count also needed an
    added dish, its drops are withheld (`withhold_count_drops`)."""
    drops = [c for c in frame.choices if c.mode == "drop"]
    reduced = frame.model_copy(update={"choices": drops})
    answer = PickerAnswer(
        choices=[ChoiceAnswer(index=i, ids=c.candidates[: c.up_to]) for i, c in enumerate(drops)],
        slots=[
            SlotAnswer(index=i, recipe_id=None, reason="no answer")
            for i in range(len(frame.slots))
        ],
    )
    return withhold_count_drops(reduced, answer)


def count_add_unfilled(frame: Frame, answer: PickerAnswer) -> bool:
    """An add slot made to meet a stated count is left without a dish."""
    filled = {s.index for s in answer.slots if s.recipe_id is not None}
    return any(s.for_count and i not in filled for i, s in enumerate(frame.slots))


def withhold_count_drops(frame: Frame, answer: PickerAnswer) -> tuple[Frame, PickerAnswer]:
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


def without_picks(answer: PickerAnswer, recipe_ids: Collection[int]) -> PickerAnswer:
    """The answer with the slots that chose one of `recipe_ids` left unfilled."""
    bad = set(recipe_ids)
    slots = [
        s.model_copy(update={"recipe_id": None, "reason": "breaks a rule"})
        if s.recipe_id in bad
        else s
        for s in answer.slots
    ]
    return answer.model_copy(update={"slots": slots})


def slot_results(frame: Frame, answer: PickerAnswer, facts: dict[int, DishFacts]) -> list[dict]:
    """Every slot (the frame's, then those the replace choices made) with the dish that fills
    it (`recipe_id`, None when nothing fits) and the picker's reason."""
    answers: dict[int, SlotAnswer] = {}
    for item in answer.slots:
        answers.setdefault(item.index, item)
    results = []
    for index, slot in enumerate(choice_slots(frame, answer, facts)):
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
    frame: Frame, slots: list[dict], titles: dict[int, str], breaking: dict[int, str]
) -> list[dict]:
    """What a change could not do, in order: named dishes not placed ("missing"), then the
    slots nothing filled: a dish that breaks a rule is removed ("removed"), any other keeps
    its dish or adds nothing ("unfilled"). `why` is why a replaced dish had to change (a
    `breaking` reason), so the next action fits it."""
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
        if slot["recipe_id"] is not None:
            continue
        replaces = slot["replaces"]
        why = breaking.get(replaces, "") if replaces is not None else ""
        if replaces is not None and slot["reason"] == "breaks_rule":
            items.append(
                {"kind": "removed", "recipe_id": replaces, "title": titles.get(replaces, ""), "why": why}
            )
            continue
        items.append(
            {
                "kind": "unfilled",
                "recipe_id": replaces,
                "dish_type": slot["dish_type"],
                "title": titles.get(replaces) if replaces is not None else None,
                "why": why,
                # An add for a stated count: the reply says the count could not be met.
                "for_count": bool(slot.get("for_count")),
            }
        )
    return items


def change_actions(items: list[dict], spec: dict, wish_unmet: bool) -> list[str]:
    """The next actions for the unfinished parts only (spec 5.5), as today's failure reasons
    map them: a dish not in the library offers importing; a dish hidden by the time limit,
    stock or cookware offers dropping the limit, shopping or registering cookware; a slot
    nothing fits (and an unmet wish) offers what an unmet wish does."""
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
    nothing filled), the named adds not placed, the typed adds no dish filled, and the wish
    when it was not met or its general choice changed nothing. A removal code refused (a
    locked dish without a quote) is not re-run."""
    refused = {item.get("id") for item in frame.ignored}
    left = set(result_ids)
    remove = [
        r.model_dump() for r in reading.remove if r.id in left and r.id not in refused
    ]
    add = [ReadAdd(name=m.name).model_dump() for m in frame.missing if m.detail.get("replaces") is None]
    unfilled_adds: dict[str, int] = {}
    for slot in slots:
        if slot["kind"] == "add" and slot["recipe_id"] is None:
            unfilled_adds[slot["dish_type"]] = unfilled_adds.get(slot["dish_type"], 0) + 1
    add += [ReadAdd(dish_type=t, count=n).model_dump() for t, n in unfilled_adds.items()]
    general = reading.wish_general and bool(reading.wish.strip())
    chose = any(c.mode == "replace" for c in frame.choices) and any(
        s["recipe_id"] is not None for s in slots[len(frame.slots) :]
    )
    general_left = general and (wish_met is False or not chose)
    if not (remove or add or general_left):
        return None
    return {
        "remove": remove,
        "add": add,
        "wish": reading.wish,
        "wish_general": general_left,
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
