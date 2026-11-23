"""The dish picker (plan 3b-2 spec 5.5): after code builds the frame of a change (`agent/frame.py`),
one narrow model call fills its slots and makes its bounded choices. This module holds the
picker's answer schema, its input after the system prompt, and the code that checks its answer
against the frame and applies it. Pure code: no model call and no database. The diner rules,
stock, cookware and the time limit of the new dishes are checked afterwards by `validate_plan`.

Slot numbering: the frame's slots are numbered 0, 1, ... in order. A dish the picker chooses in
a "replace" choice becomes one more slot of that dish's own type; these slots are numbered after
the frame's slots, in the order of the choices and, within a choice, of the ids the picker
lists (`choice_slots`)."""

from collections.abc import Collection
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, Field

from minemydinner.agent.frame import Choice, Frame, Slot, fold
from minemydinner.agent.revision import DishFacts
from minemydinner.planning.validate import PlanSpec

# A picker answer with problems is retried once with them (spec 5.7).
PICK_MAX_ATTEMPTS = 2


class ChoiceAnswer(BaseModel):
    """The dishes chosen in one choice."""

    index: int = Field(description="The number of the choice, as listed (Choice 0, Choice 1, ...).")
    ids: list[int] = Field(
        description=(
            "Ids of the chosen dishes, all from that choice's list. A \"drop\" choice takes "
            "exactly its number; a \"replace\" choice takes 1 up to its number, and each dish "
            "chosen becomes a new slot of its own type, numbered after the listed slots."
        )
    )


class SlotAnswer(BaseModel):
    """The dish chosen for one slot, or none."""

    index: int = Field(
        description=(
            "The number of the slot (Slot 0, Slot 1, ...); the slots made by a replace choice "
            "come after the listed ones, in the order of the choices and of their ids."
        )
    )
    recipe_id: int | None = Field(
        description=(
            "The id of a library dish of the slot's dish type, not in the current plan and "
            "not chosen for another slot; null when nothing fits."
        )
    )
    reason: str = Field(
        default="", description="When recipe_id is null: a short reason why nothing fits."
    )


class PickerAnswer(BaseModel):
    """The picker's answer: every choice made and every slot answered."""

    choices: list[ChoiceAnswer] = Field(
        default_factory=list, description="One answer per listed choice."
    )
    slots: list[SlotAnswer] = Field(
        default_factory=list,
        description="One answer per slot, including the slots your replace choices make.",
    )
    wish_met: bool | None = Field(
        default=None,
        description="Whether the new dishes meet the user's wish. None when there is no wish.",
    )
    note: str = Field(
        default="",
        description=(
            "One or two sentences to the user, in the reply locale, on why the new dishes fit."
        ),
    )


class PickOutcome(BaseModel):
    """The plan after the picker's answer. With `problems`, nothing is applied: `recipe_ids`
    is the plan as it was and `unfilled` is empty. Otherwise `unfilled` holds the slots left
    without a dish (frame slots and choice slots)."""

    recipe_ids: list[int]
    problems: list[str]
    unfilled: list[Slot]


def _article(dish_type: str) -> str:
    return f"an {dish_type}" if dish_type[:1] in "aeiou" else f"a {dish_type}"


def _first_by_index(answers: list, count: int, kind: str, problems: list[str]) -> dict:
    """The answers by index; an index that does not exist or repeats is a problem."""
    by_index: dict = {}
    for answer in answers:
        if not 0 <= answer.index < count:
            problems.append(f"{kind} {answer.index} does not exist.")
        elif answer.index in by_index:
            problems.append(f"{kind} {answer.index} is answered twice.")
        else:
            by_index[answer.index] = answer
    return by_index


def _chosen(choice: Choice, answer: ChoiceAnswer | None) -> list[int]:
    """The answer's ids that are the choice's candidates, once each, in the answer's order."""
    ids = answer.ids if answer else []
    return [i for i in dict.fromkeys(ids) if i in choice.candidates]


def choice_slots(frame: Frame, answer: PickerAnswer, facts: dict[int, DishFacts]) -> list[Slot]:
    """Every slot to fill: the frame's slots, then a replace slot for each dish chosen in a
    replace choice (its own dish type), in the order of the choices and of the chosen ids."""
    by_index: dict[int, ChoiceAnswer] = {}
    for item in answer.choices:
        by_index.setdefault(item.index, item)
    slots = list(frame.slots)
    for index, choice in enumerate(frame.choices):
        if choice.mode != "replace":
            continue
        for dish_id in _chosen(choice, by_index.get(index)):
            facts_of = facts.get(dish_id)
            dish_type = facts_of.dish_type if facts_of else ""
            slots.append(
                Slot(
                    kind="replace",
                    dish_type=dish_type,
                    replaces=dish_id,
                    must_use=list(choice.must_use),
                )
            )
    return slots


def must_use_misses(
    frame: Frame, answer: PickerAnswer, uncovered: Collection[str]
) -> tuple[list[str], set[int]]:
    """Spec 5.4, 5.5: a replace choice made for a must-use ingredient no dish used, or the add
    slot that takes its place, must bring a dish that uses it. `uncovered` holds the must-use
    names the plan after the answer still misses (validation's H7). For each such choice or
    slot whose ingredient is still missed, a problem naming the dishes it got, and those
    dishes, so a last attempt leaves them out: a replaced dish stays and an add adds nothing
    (the plan never shrinks unasked). A slot answered null is not a problem (nothing fits),
    and a name without a choice or slot (missed before this turn) is never checked here.
    Slots are numbered as `choice_slots` numbers them."""
    missed = {fold(n) for n in uncovered}
    by_index: dict[int, ChoiceAnswer] = {}
    for item in answer.choices:
        by_index.setdefault(item.index, item)
    fills: dict[int, int | None] = {}
    for item in answer.slots:
        fills.setdefault(item.index, item.recipe_id)
    # (what the model calls it, its slot numbers, the names it is for)
    held: list[tuple[str, range, list[str]]] = [
        (f"Slot {i}", range(i, i + 1), slot.must_use)
        for i, slot in enumerate(frame.slots)
        if slot.must_use
    ]
    position = len(frame.slots)
    for index, choice in enumerate(frame.choices):
        if choice.mode != "replace":
            continue
        count = len(_chosen(choice, by_index.get(index)))
        held.append((f"Choice {index}", range(position, position + count), choice.must_use))
        position += count
    problems: list[str] = []
    bad: set[int] = set()
    for label, own, must in held:
        names = [n for n in must if fold(n) in missed]
        picked = [fills[i] for i in own if fills.get(i) is not None]
        if not names or not picked:
            continue
        need = ", ".join(names)
        problems.append(
            f"{label} is for {need}, which no dish in the plan uses, but the new dish "
            f"({_ids(picked)}) does not use {need}. Choose a dish that uses {need}, or answer "
            "null for that slot when none fits."
        )
        bad.update(picked)
    return problems, bad


def _check_choices(frame: Frame, answer: PickerAnswer, problems: list[str]) -> list[list[int]]:
    """The chosen ids of each choice, with a problem for an id outside the candidates, a wrong
    number, or a dish chosen twice (in one choice or by two choices)."""
    by_index = _first_by_index(answer.choices, len(frame.choices), "Choice", problems)
    seen: set[int] = set()
    chosen_all: list[list[int]] = []
    for index, choice in enumerate(frame.choices):
        item = by_index.get(index)
        ids = item.ids if item else []
        if item is None:
            problems.append(f"Choice {index} has no answer.")
        for dish_id in ids:
            if dish_id not in choice.candidates:
                problems.append(
                    f"Choice {index}: dish {dish_id} is not one of its dishes "
                    f"({', '.join(map(str, choice.candidates))})."
                )
            elif dish_id in seen:
                problems.append(f"Dish {dish_id} is chosen twice.")
            seen.add(dish_id)
        if item is not None:
            count = len(ids)
            if choice.mode == "drop" and count != choice.up_to:
                problems.append(
                    f"Choice {index} must drop exactly {choice.up_to} dishes, not {count}."
                )
            elif choice.mode == "replace" and not 1 <= count <= choice.up_to:
                problems.append(
                    f"Choice {index} must replace at least 1 and at most {choice.up_to} "
                    f"dishes, not {count}."
                )
        chosen_all.append(_chosen(choice, item))
    return chosen_all


def _check_slots(
    plan_ids: list[int],
    frame: Frame,
    slots: list[Slot],
    answer: PickerAnswer,
    facts: dict[int, DishFacts],
    problems: list[str],
) -> dict[int, int | None]:
    """The dish of each slot (None: nothing fits), with a problem for a slot without an answer,
    an unknown dish, a dish of the wrong type, a dish already in the plan, or a dish picked
    twice."""
    by_index = _first_by_index(answer.slots, len(slots), "Slot", problems)
    taken = set(plan_ids) | set(frame.placed)
    picked: set[int] = set()
    fills: dict[int, int | None] = {}
    for index, slot in enumerate(slots):
        item = by_index.get(index)
        if item is None:
            problems.append(f"Slot {index} has no answer; give a dish id or null.")
            continue
        recipe_id = item.recipe_id
        fills[index] = recipe_id
        if recipe_id is None:
            continue
        found = facts.get(recipe_id)
        if found is None:
            problems.append(f"Slot {index}: dish {recipe_id} is not in the recipe library.")
        elif found.dish_type != slot.dish_type:
            problems.append(
                f"Slot {index} needs {_article(slot.dish_type)}, but dish {recipe_id} is "
                f"{_article(found.dish_type)}."
            )
        if recipe_id in taken:
            problems.append(f"Slot {index}: dish {recipe_id} is already in the plan.")
        elif recipe_id in picked:
            problems.append(f"Slot {index}: dish {recipe_id} is picked twice.")
        picked.add(recipe_id)
    return fills


def apply_picks(
    plan_ids: list[int], frame: Frame, answer: PickerAnswer, facts: dict[int, DishFacts]
) -> PickOutcome:
    """Check the picker's answer against the frame and apply it. `facts` must cover the plan's
    dishes and every dish the answer names (a named dish without facts is unknown).

    The result, in plan order: a dropped dish (the frame's drops, a drop choice) leaves; a
    replaced dish gives its place to its slot's dish; an unfilled replace slot keeps its dish,
    except a "breaks_rule" slot, whose dish is removed (spec 5.5); then the placed dishes, then
    the filled add slots. Locked dishes are never choice candidates, so they never leave here."""
    problems: list[str] = []
    chosen = _check_choices(frame, answer, problems)
    slots = choice_slots(frame, answer, facts)
    fills = _check_slots(plan_ids, frame, slots, answer, facts, problems)
    if problems:
        return PickOutcome(recipe_ids=list(plan_ids), problems=problems, unfilled=[])

    leaving = set(frame.drops)
    for choice, ids in zip(frame.choices, chosen):
        if choice.mode == "drop":
            leaving.update(ids)
    in_place: dict[int, int] = {}
    added: list[int] = []
    unfilled: list[Slot] = []
    for index, slot in enumerate(slots):
        recipe_id = fills.get(index)
        if recipe_id is None:
            unfilled.append(slot)
            if slot.replaces is not None and slot.reason == "breaks_rule":
                leaving.add(slot.replaces)
        elif slot.replaces is not None:
            in_place[slot.replaces] = recipe_id
        else:
            added.append(recipe_id)
    result = [in_place.get(i, i) for i in plan_ids if i not in leaving]
    result += [i for i in frame.placed if i not in result]
    result += added
    return PickOutcome(recipe_ids=list(dict.fromkeys(result)), problems=[], unfilled=unfilled)


def _ids(ids: list[int]) -> str:
    return ", ".join(map(str, ids))


def _choice_line(index: int, choice: Choice) -> str:
    kind = choice.dish_type or "any type"
    dishes = _ids(choice.candidates)
    if choice.mode == "drop":
        return f"- Choice {index}: drop exactly {choice.up_to} of {dishes} ({kind})"
    line = (
        f"- Choice {index}: replace 1 to {choice.up_to} of {dishes} ({kind}); "
        "each dish you choose becomes a new slot of its own dish type"
    )
    if choice.must_use:
        line += f"; the new dish must use {', '.join(choice.must_use)}, which no dish uses yet"
    return line


def conditions_line(spec: PlanSpec) -> str:
    """The meal's hard conditions the new dishes are checked against (spec 5.5), one line."""
    mode = (
        "fridge only (new dishes use what is at home)"
        if spec.mode == "fridge_only"
        else "shopping allowed"
    )
    limit = f"{spec.time_limit_min} minutes" if spec.time_limit_min else "none"
    must = ", ".join(spec.must_use) or "nothing"
    return f"Meal conditions: {mode}; time limit: {limit}; must use in at least one dish: {must}"


def _slot_line(index: int, slot: Slot) -> str:
    where = f" in place of {slot.replaces}" if slot.replaces is not None else ", added"
    line = f"- Slot {index}: {_article(slot.dish_type)}{where}"
    if slot.must_use:
        line += f"; it must use {', '.join(slot.must_use)}, which no dish uses yet"
    return line


def picker_messages(
    frame: Frame,
    plan_lines: str,
    rules_text: str,
    wish: str,
    shortlist: str,
    locale: str,
    conditions: str = "",
) -> list[BaseMessage]:
    """The picker's input after the system prompt (spec 5.5): one user message with the
    shortlist, the current plan, the diner and meal rules, the meal's conditions
    (`conditions_line`), the user's wish (in the user's words), what code already decided,
    the choices with their limits (a must-use choice names its ingredient), the slots with
    their dish types and how choice slots are numbered, and the reply locale."""
    decided = []
    if frame.drops:
        decided.append(f"- Leaving the plan: {_ids(frame.drops)}")
    if frame.placed:
        decided.append(f"- Added by name: {_ids(frame.placed)}")
    parts = [shortlist, plan_lines, rules_text]
    if conditions:
        parts.append(conditions)
    parts.append(f"The user's wish: {wish.strip() or 'none'}")
    if decided:
        parts.append("Already decided:\n" + "\n".join(decided))
    if frame.choices:
        lines = [_choice_line(i, c) for i, c in enumerate(frame.choices)]
        parts.append("Choices (which current dishes go):\n" + "\n".join(lines))
    slot_lines = [_slot_line(i, s) for i, s in enumerate(frame.slots)] or ["- none listed"]
    slots_text = "Slots (new dishes to choose):\n" + "\n".join(slot_lines)
    if any(c.mode == "replace" for c in frame.choices):
        slots_text += (
            f"\nSlots for the dishes you choose to replace are numbered from "
            f"{len(frame.slots)}, in the order of the choices and of the ids you list."
        )
    parts.append(slots_text)
    parts.append(f"Reply locale: {locale}")
    return [HumanMessage("\n\n".join(parts))]
