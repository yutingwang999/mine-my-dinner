"""The dish picker (plan 3b-2 spec 5.5): after code builds the frame of a change (`agent/frame.py`),
one narrow model call fills its slots and makes its bounded choices. This module holds the
picker's answer schema, its input after the system prompt, and the code that checks its answer
against the frame and applies it. Pure code: no model call and no database. The diner rules,
stock, cookware and the time limit of the new dishes are checked afterwards by `validate_plan`.

Code checks, it does not interpret (spec 5.4, amended 2026-11-23): a replace slot takes a dish
of any type, so the picker chooses what kind of dish takes a dish's place; code checks an add's
type, the stated counts (`Frame.count_ranges`), the rules and conditions, and for a named slot
nothing beyond those (the picker judges which dish the user's words mean).

Slot numbering: the frame's slots are numbered 0, 1, ... in order. A dish the picker chooses in
a "replace" choice becomes one more slot in that dish's place; these slots are numbered after
the frame's slots, in the order of the choices and, within a choice, of the ids the picker
lists (`choice_slots`)."""

from collections.abc import Collection
import json
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, Field, ValidationError

from minemydinner.agent.frame import Choice, Frame, Slot, fold
from minemydinner.agent.revision import DishFacts
from minemydinner.planning.composition import (
    DISH_TYPE_OF_CATEGORY,
    SPLIT_CATEGORIES,
    count_categories,
)
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
            "chosen becomes a new slot in its place, numbered after the listed slots."
        )
    )


class SlotPick(BaseModel):
    """The dish chosen for one slot, or none (code's form of a `SlotAnswer`)."""

    index: int
    recipe_id: int | None = None
    reason: str = ""


class Picks(BaseModel):
    """The picker's answer as code checks and applies it (`PickerAnswer.picks`): a slot's
    `recipe_id` is None when nothing fits, and `wish_met` is None when there is no wish or it
    is unknown (a salvaged answer, `salvaged_picks`)."""

    choices: list[ChoiceAnswer] = Field(default_factory=list)
    slots: list[SlotPick] = Field(default_factory=list)
    wish_met: bool | None = None
    note: str = ""


# The model-facing answer (pick_v3, Task 11 fix 3): no nullable field, since DeepSeek cut the
# picker's tool-call arguments right after a nullable field in development runs 1 to 3. Its
# field order is choices, slots, note, wish_result, so what code needs most comes first.
# pick_v4 (Task 11 fix 4): DeepSeek still cut answers right after the key "wish_met", and a
# replay with the field named wish_result had none cut, so the model sees wish_result; code
# keeps wish_met (`Picks`, the records, the reply).
NOTHING_FITS = 0
WISH_MET = {"yes": True, "no": False, "no_wish": None}


class SlotAnswer(BaseModel):
    """The dish chosen for one slot, or 0 when nothing fits."""

    index: int = Field(
        description=(
            "The number of the slot (Slot 0, Slot 1, ...); the slots made by a replace choice "
            "come after the listed ones, in the order of the choices and of their ids."
        )
    )
    recipe_id: int = Field(
        description=(
            "The id of a library dish, not in the current plan and not chosen for another "
            "slot; for an added dish of a stated type, a dish of that type; 0 when nothing "
            "fits."
        )
    )
    reason: str = Field(
        default="", description="When recipe_id is 0: a short reason why nothing fits."
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
    note: str = Field(
        default="",
        description=(
            "One or two sentences to the user, in the reply locale, on why the new dishes fit."
        ),
    )
    wish_result: Literal["yes", "no", "no_wish"] = Field(
        description=(
            "\"yes\" when the new dishes meet the user's wish, \"no\" when they do not, "
            '"no_wish" when there is no wish.'
        ),
    )

    def picks(self) -> Picks:
        """Code's form: recipe id 0 becomes None, and wish_result "yes" / "no" / "no_wish"
        becomes wish_met True / False / None."""
        return Picks(
            choices=[c.model_copy() for c in self.choices],
            slots=[
                SlotPick(
                    index=s.index,
                    recipe_id=None if s.recipe_id == NOTHING_FITS else s.recipe_id,
                    reason=s.reason,
                )
                for s in self.slots
            ],
            wish_met=WISH_MET[self.wish_result],
            note=self.note,
        )

    @classmethod
    def of(cls, picks: Picks) -> "PickerAnswer":
        """The model-facing form of `picks` (the inverse of `picks()`)."""
        wish = next(k for k, v in WISH_MET.items() if v is picks.wish_met)
        return cls(
            choices=[c.model_copy() for c in picks.choices],
            slots=[
                SlotAnswer(
                    index=s.index,
                    recipe_id=NOTHING_FITS if s.recipe_id is None else s.recipe_id,
                    reason=s.reason,
                )
                for s in picks.slots
            ],
            note=picks.note,
            wish_result=wish,
        )


def _complete_fields(text: str) -> dict:
    """The top-level fields of a JSON object cut short, each whose value closed before the
    cut; parsing stops at the first field that did not."""
    decoder = json.JSONDecoder()
    fields: dict = {}

    def skip(at: int) -> int:
        while at < len(text) and text[at] in " \t\r\n":
            at += 1
        return at

    position = skip(0)
    if not text.startswith("{", position):
        return fields
    position += 1
    while True:
        position = skip(position)
        try:
            key, position = decoder.raw_decode(text, position)
            position = skip(position)
            if not isinstance(key, str) or not text.startswith(":", position):
                return fields
            value, position = decoder.raw_decode(text, skip(position + 1))
        except ValueError:
            return fields
        fields[key] = value
        position = skip(position)
        if not text.startswith(",", position):
            return fields
        position += 1


def salvaged_picks(args: str) -> tuple[Picks, bool] | None:
    """Task 11 fix 3: a `PickerAnswer` tool call whose arguments were cut short (DeepSeek ends
    them silently, server side; LangChain keeps the call in `invalid_tool_calls`). What closed
    before the cut is kept: the choices and the slots, and whether `wish_met` is known. A
    closed, valid `wish_result` (the schema puts it last, so the answer lacks at most its
    closing brace) is kept with the note; otherwise the note is empty and wish_met unknown
    (None).
    Returns the answer and whether wish_met is known; None when the slots did not close (no
    answer), or when what closed is not a valid answer."""
    fields = _complete_fields(args)
    if "slots" not in fields:
        return None
    try:
        slots = [SlotAnswer.model_validate(s) for s in fields["slots"]]
        choices = [ChoiceAnswer.model_validate(c) for c in fields.get("choices") or []]
    except (ValidationError, TypeError):
        return None
    wish = fields.get("wish_result")
    note = fields.get("note")
    if wish in WISH_MET and isinstance(note, str | None):
        answer = PickerAnswer(choices=choices, slots=slots, note=note or "", wish_result=wish)
        return answer.picks(), True
    return PickerAnswer(choices=choices, slots=slots, wish_result="no_wish").picks(), False


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


def choice_slots(frame: Frame, answer: Picks) -> list[Slot]:
    """Every slot to fill: the frame's slots, then a replace slot (no dish type) for each dish
    chosen in a replace choice, in the order of the choices and of the chosen ids."""
    by_index: dict[int, ChoiceAnswer] = {}
    for item in answer.choices:
        by_index.setdefault(item.index, item)
    slots = list(frame.slots)
    for index, choice in enumerate(frame.choices):
        if choice.mode != "replace":
            continue
        for dish_id in _chosen(choice, by_index.get(index)):
            slots.append(Slot(kind="replace", replaces=dish_id, must_use=list(choice.must_use)))
    return slots


def must_use_misses(
    frame: Frame, answer: Picks, uncovered: Collection[str]
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
            "0 for that slot when none fits."
        )
        bad.update(picked)
    return problems, bad


def _check_choices(frame: Frame, answer: Picks, problems: list[str]) -> list[list[int]]:
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


# The two side types: an add of no type is one of them (3b-2 design 5.4).
_SIDE_TYPES = tuple(DISH_TYPE_OF_CATEGORY[c] for c in SPLIT_CATEGORIES)


def _untyped_add(slot: Slot) -> bool:
    """An add of no type and no name: one more meat or vegetable dish, either kind."""
    return slot.replaces is None and not slot.dish_type and not slot.named


def _check_slots(
    plan_ids: list[int],
    frame: Frame,
    slots: list[Slot],
    answer: Picks,
    facts: dict[int, DishFacts],
    problems: list[str],
) -> dict[int, int | None]:
    """The dish of each slot (None: nothing fits), with a problem for a slot without an answer,
    an unknown dish, an add of a stated type filled with another type, an add of no type (no
    name either) filled with a dish that is no meat or vegetable dish (it counts toward the dish
    total, 3b-2 design 5.4), a dish already in the plan, or a dish picked twice. A replace slot
    and a named slot take any type."""
    by_index = _first_by_index(answer.slots, len(slots), "Slot", problems)
    taken = set(plan_ids) | set(frame.placed)
    picked: set[int] = set()
    fills: dict[int, int | None] = {}
    for index, slot in enumerate(slots):
        item = by_index.get(index)
        if item is None:
            problems.append(
                f"Slot {index} has no answer; give a dish id, or 0 when nothing fits."
            )
            continue
        recipe_id = item.recipe_id
        fills[index] = recipe_id
        if recipe_id is None:
            continue
        found = facts.get(recipe_id)
        if found is None:
            problems.append(f"Slot {index}: dish {recipe_id} is not in the recipe library.")
        elif slot.dish_type and found.dish_type != slot.dish_type:
            problems.append(
                f"Slot {index} needs {_article(slot.dish_type)}, but dish {recipe_id} is "
                f"{_article(found.dish_type)}."
            )
        elif _untyped_add(slot) and found.dish_type not in _SIDE_TYPES:
            problems.append(f"Slot {index} must be a meat or vegetable dish.")
        if recipe_id in taken:
            problems.append(f"Slot {index}: dish {recipe_id} is already in the plan.")
        elif recipe_id in picked:
            problems.append(f"Slot {index}: dish {recipe_id} is picked twice.")
        picked.add(recipe_id)
    return fills


# A count field in the picker's words.
_COUNT_WORDS = {
    "dishes": "dishes (meat and vegetable dishes together)",
    "meat_dishes": "meat dishes",
    "vegetable_dishes": "vegetable dishes",
    "soups": "soups",
    "staples": "staple foods",
    "desserts": "desserts",
    "snacks": "snacks",
}


def _range_text(low: int, high: int) -> str:
    return f"exactly {low}" if low == high else f"{low} to {high}"


def _count_problems(
    frame: Frame,
    result: list[int],
    slots: list[Slot],
    fills: dict[int, int | None],
    facts: dict[int, DishFacts],
) -> list[str]:
    """The stated counts the picker's dishes break (spec 5.4: stated counts are invariants).
    A count is the picker's problem only when its dishes move it out of the stated range and
    the dishes the frame planned would have kept it in (a replace slot counted as the type of
    the dish it replaces, an add as its own type): a count the frame itself could not meet,
    or a slot left empty, is never the picker's."""
    if not frame.count_ranges:
        return []

    def type_of(recipe_id: int) -> str:
        found = facts.get(recipe_id)
        return found.dish_type if found else ""

    planned_of: dict[int, str] = {}
    for index, slot in enumerate(slots):
        recipe_id = fills.get(index)
        if recipe_id is None:
            continue
        if slot.dish_type:
            planned_of[recipe_id] = slot.dish_type
        elif slot.replaces is not None:
            planned_of[recipe_id] = type_of(slot.replaces)
    actual = count_categories(type_of(i) for i in result)
    planned = count_categories(planned_of.get(i) or type_of(i) for i in result)
    problems = []
    for name, (low, high) in frame.count_ranges.items():
        if not low <= actual[name] <= high and low <= planned[name] <= high:
            problems.append(
                f"The user stated {_range_text(low, high)} {_COUNT_WORDS[name]}, but with your "
                f"dishes the plan has {actual[name]}. Keep the stated counts: choose dishes "
                "that keep them, or 0 for a slot when none fits."
            )
    return problems


def apply_picks(
    plan_ids: list[int], frame: Frame, answer: Picks, facts: dict[int, DishFacts]
) -> PickOutcome:
    """Check the picker's answer against the frame and apply it. `facts` must cover the plan's
    dishes and every dish the answer names (a named dish without facts is unknown).

    The result, in plan order: a dropped dish (the frame's drops, a drop choice) leaves; a
    replaced dish gives its place to its slot's dish; an unfilled replace slot keeps its dish,
    except a "breaks_rule" slot, whose dish is removed (spec 5.5); then the placed dishes, then
    the filled add slots. Locked dishes are never choice candidates, so they never leave here.
    A result that breaks a stated count (`_count_problems`) is a problem too."""
    problems: list[str] = []
    chosen = _check_choices(frame, answer, problems)
    slots = choice_slots(frame, answer)
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
    result = list(dict.fromkeys(result))
    problems = _count_problems(frame, result, slots, fills, facts)
    if problems:
        return PickOutcome(recipe_ids=list(plan_ids), problems=problems, unfilled=[])
    return PickOutcome(recipe_ids=result, problems=[], unfilled=unfilled)


def _ids(ids: list[int]) -> str:
    return ", ".join(map(str, ids))


def _choice_line(index: int, choice: Choice) -> str:
    kind = choice.dish_type or "any type"
    dishes = _ids(choice.candidates)
    if choice.mode == "drop":
        return f"- Choice {index}: drop exactly {choice.up_to} of {dishes} ({kind})"
    line = (
        f"- Choice {index}: replace 1 to {choice.up_to} of {dishes} ({kind}); "
        "each dish you choose becomes a new slot in its place"
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
    """A slot in the picker's input: the dish it replaces (no dish type: the picker chooses the
    kind) and the user's words for it, or an add's type (an add of no type and no name is a
    meat or vegetable dish, either kind); a named slot asks for the library dish the user's
    words mean, by its title or by its main ingredients."""
    if slot.replaces is not None:
        line = f"- Slot {index}: in place of {slot.replaces}"
        if slot.words.strip():
            line += f', which goes because of the user\'s words "{slot.words.strip()}"'
            if not slot.named:
                line += (
                    "; choose the new dish from the wish and the meal's rules, "
                    "not from these words"
                )
        if slot.for_wish:
            line += ", for the wish"
    elif slot.dish_type:
        line = f"- Slot {index}: one more {slot.dish_type} the user asked for"
    elif slot.named:
        line = f"- Slot {index}: one more dish the user asked for, of any type"
    else:
        line = f"- Slot {index}: one more meat or vegetable dish the user asked for, either kind"
    if slot.for_wish and slot.replaces is None:
        line += ", for the wish"
    if slot.named:
        line += (
            f'; the user named "{slot.named}": the library dish these words mean, by its '
            "title or by its main ingredients, or 0 when none is"
        )
    if slot.must_use:
        line += f"; it must use {', '.join(slot.must_use)}, which no dish uses yet"
    return line


def _stated_counts_line(frame: Frame, plan_counts: dict[str, int] | None) -> str:
    """The stated counts, each with the plan's number after what code already decided (its
    drops and the dishes placed by name) when `plan_counts` is given: "dishes (meat and
    vegetable dishes, soups not included): 3 in the plan after what is already decided,
    exactly 4 stated". Code facts only."""
    items = []
    for name, (low, high) in frame.count_ranges.items():
        stated = _range_text(low, high)
        if plan_counts is None:
            items.append(f"{name.replace('_', ' ')}: {stated}")
            continue
        label = (
            "dishes (meat and vegetable dishes, soups not included)"
            if name == "dishes"
            else name.replace("_", " ")
        )
        items.append(
            f"{label}: {plan_counts.get(name, 0)} in the plan after what is already decided, "
            f"{stated} stated"
        )
    return f"Stated counts (code checks the result): {'; '.join(items)}"


def picker_messages(
    frame: Frame,
    plan_lines: str,
    rules_text: str,
    wish: str,
    shortlist: str,
    locale: str,
    conditions: str = "",
    plan_counts: dict[str, int] | None = None,
) -> list[BaseMessage]:
    """The picker's input after the system prompt (spec 5.5): one user message with the
    shortlist, the current plan, the diner and meal rules, the meal's conditions
    (`conditions_line`), the counts the user stated (with `plan_counts`, the plan's number
    after what code already decided next to each, `count_categories` of those dishes: Task 11 fix 2, so a soup is never
    counted as a dish), the user's wish (in the user's words),
    what code already decided, the choices with their limits (a must-use choice names its
    ingredient), the slots (the dish each replaces with the user's words, an add's type, a
    named dish) and how choice slots are numbered, and the reply locale."""
    decided = []
    if frame.drops:
        decided.append(f"- Leaving the plan: {_ids(frame.drops)}")
    if frame.placed:
        decided.append(f"- Added by name: {_ids(frame.placed)}")
    parts = [shortlist, plan_lines, rules_text]
    if conditions:
        parts.append(conditions)
    if frame.count_ranges:
        parts.append(_stated_counts_line(frame, plan_counts))
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
