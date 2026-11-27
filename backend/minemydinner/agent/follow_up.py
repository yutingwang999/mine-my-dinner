"""The follow-up reading (plan 3b-2 spec 5.2, 5.3): one model reads a follow-up message on an
existing plan once into a `FollowUpReading`. The reading says what the message wants, never
which new dishes (the picker's job) and never the final counts (code's job). This module holds
the schema, the reader's input and the request after a `change` reading. Pure code: no model
call and no database."""

from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field

from minemydinner.agent.change_rules import COUNT_GROUP, unset


class ReadRemoval(BaseModel):
    """One dish of the current plan that the message removes."""

    id: int = Field(description="The id of the dish in the current plan.")
    mode: Literal["drop", "replace"] = Field(
        description=(
            "\"drop\": the words only say not to have it, the plan gets smaller. \"replace\": "
            "the words want another dish in its place."
        )
    )
    quote: str = Field(
        description=(
            "The user's own words from the latest message that point at this dish, copied "
            "character for character."
        )
    )
    with_name: str = Field(
        default="",
        description=(
            "Only for \"replace\": the replacement dish the user named, in the user's words. "
            "Empty when the user named none."
        ),
    )
    for_wish: bool = Field(
        default=False,
        description="True when this replacement is there only to meet the wish.",
    )


class ReadAdd(BaseModel):
    """Dishes the message adds."""

    dish_type: str | None = Field(
        default=None,
        description=(
            "The dish type to add (meat dish, vegetable dish, soup, staple food, dessert, "
            "snack), or null when the user named a dish or stated no type."
        ),
    )
    count: int = Field(default=1, description="How many dishes of this type to add.")
    name: str = Field(
        default="",
        description="A dish the user named, in the user's words. Empty when none was named.",
    )
    for_wish: bool = Field(
        default=False,
        description="True when this add is there only to meet the wish.",
    )


class ReadCounts(BaseModel):
    """Only the counts the message itself states, as absolute numbers for the whole plan."""

    dishes: int | None = Field(default=None, description="Total of meat and vegetable dishes.")
    meat_dishes: int | None = Field(default=None, description="Meat dishes.")
    vegetable_dishes: int | None = Field(default=None, description="Vegetable dishes.")
    soups: int | None = Field(default=None, description="Soups.")
    staples: int | None = Field(default=None, description="Staple foods.")
    desserts: int | None = Field(default=None, description="Desserts.")
    snacks: int | None = Field(default=None, description="Snacks.")


class ReadConditions(BaseModel):
    """Changes to the conditions that the message states."""

    mode: Literal["fridge_only", "shopping"] | None = Field(
        default=None,
        description="Set only when the message changes it: fridge_only or shopping.",
    )
    time_limit_min: int | None = Field(
        default=None, description="A new time limit in minutes, only when the message states one."
    )
    clear_time_limit: bool = Field(
        default=False, description="True when the message removes the time limit."
    )
    must_use: list[str] = Field(
        default_factory=list,
        description="Ingredients the message says must be used. Empty when it states none.",
    )
    prefer_use: list[str] = Field(
        default_factory=list,
        description="Ingredients the message prefers. Empty when it states none.",
    )


class FollowUpReading(BaseModel):
    """What a follow-up message on the current plan wants."""

    kind: Literal["change", "new_meal", "people", "other"] = Field(
        description=(
            "\"change\": it changes the current plan. \"new_meal\": it asks for a different, "
            "new meal. \"people\": it changes who eats. \"other\": anything else."
        )
    )
    ambiguous: bool = Field(
        default=False,
        description="True when the message can be read in more than one way.",
    )
    reading: str = Field(
        default="",
        description=(
            "One short sentence in the reply locale saying how you understood the message; it "
            "is shown to the user only when ambiguous is true."
        ),
    )
    remove: list[ReadRemoval] = Field(
        default_factory=list,
        description="Dishes of the current plan the message removes, each by id.",
    )
    add: list[ReadAdd] = Field(
        default_factory=list,
        description="Dishes the message adds, each as a dish type and count or a named dish.",
    )
    wish: str = Field(
        default="",
        description=(
            "A taste or style wish the new dishes should meet, in the user's words. Empty "
            "when the message states none."
        ),
    )
    wish_targets: list[int] = Field(
        default_factory=list,
        description=(
            "When the wish is about dishes of the plan rather than a dish this message already "
            "removes or adds: the ids of the dishes it touches, most touched first. Empty when "
            "it touches none."
        ),
    )
    meal_avoid: list[str] = Field(
        default_factory=list,
        description=(
            "The WHOLE list of things to leave out of this meal after this message, so a "
            "withdrawn wish is simply missing from it."
        ),
    )
    counts: ReadCounts = Field(
        default_factory=ReadCounts,
        description=(
            "Only counts the message itself states, absolute for the whole plan; all null "
            "when it states none."
        ),
    )
    conditions: ReadConditions = Field(
        default_factory=ReadConditions,
        description="Changes to mode, time limit and named ingredients that the message states.",
    )


def hand_edit_line(log: list[dict], titles: dict[int, str]) -> str:
    """What the user changed by hand on the current plan after it was proposed, from its card's
    edit log (`agent/edits.py`): adds, removals and a restore, in order, dishes by title (Task
    11 fix 2: hand edits add no message, so a reply before them names the plan as it was and a
    removed dish is invisible in the plan lines). The wording holds whatever came after the
    edits: an "other" reply or a change that kept the plan leaves the plan and its log as they
    are, so "since the last reply" would be false (fix round 1). A keep confirms a conflict
    and changes no dish. Empty when nothing changed. Facts only, never a person.

    The restore branch is not reached by today's edit model: a restore makes the restored
    card current, and its log starts empty (the restore entry stays on the manual version in
    `plan_versions`). It is kept so a log that ever holds one still reads truthfully."""
    changes = []
    for entry in log:
        action = entry.get("action")
        if action == "restore":
            changes.append("restored the proposed plan")
        elif action in ("add", "remove"):
            title = titles.get(entry.get("recipe_id"))
            verb = "added" if action == "add" else "removed"
            changes.append(f"{verb} {title}" if title else f"{verb} a dish")
    if not changes:
        return ""
    return "The user changed this plan by hand after it was proposed: " + "; ".join(changes) + "."


def reader_messages(
    conversation: list[BaseMessage],
    plan_lines: str,
    diner_lines: list[str],
    conditions_text: str,
    locale: str,
    hand_edits: str = "",
) -> list[BaseMessage]:
    """The reader's input after the system prompt (spec 5.3): one user message with the plan
    lines, what the user changed by hand on this plan after it was proposed (`hand_edit_line`,
    when anything), the diner lines (anonymous "Diner N" rules, never a saved nickname), the
    current conditions and the whole conversation, in that order."""
    turns = []
    for message in conversation:
        who = "Assistant" if isinstance(message, AIMessage) else "User"
        turns.append(f"{who}: {message.content}")
    parts = [
        plan_lines,
        *([hand_edits] if hand_edits else []),
        "Diners:\n" + ("\n".join(diner_lines) or "- no diner rules"),
        conditions_text,
        "Conversation:\n" + "\n".join(turns),
        f"Reply locale: {locale}",
    ]
    return [HumanMessage("\n\n".join(parts))]


def request_after(request: dict, reading: FollowUpReading) -> dict:
    """The request after a `change` reading. Stated counts replace the whole count group of the
    old request (as `carry_unstated` treats it); conditions replace their own fields;
    `meal_avoid` is the reading's whole list; a stated wish replaces the query. Everything
    else is carried.

    `counts_stated_now` says whether this message states counts, never an earlier turn's: the
    change path then meets the stated counts, and otherwise keeps the plan's own (a hand-grown
    plan keeps its size). A button pressed later on this turn's card reads it the same way
    (`on_plan_counts` rebases an unstated request on the plan shown)."""
    new = dict(request)
    stated = {k: v for k, v in reading.counts.model_dump().items() if v is not None}
    new["counts_stated_now"] = bool(stated)
    if stated:
        for name in COUNT_GROUP:
            new[name] = [] if name == "range_fields" else None
        new.update(stated)
    cond = reading.conditions
    if cond.mode is not None:
        new["mode"] = cond.mode
    if cond.clear_time_limit:
        new["time_limit_min"] = None
    elif cond.time_limit_min is not None:
        new["time_limit_min"] = cond.time_limit_min
    for name in ("must_use", "prefer_use"):
        if not unset(getattr(cond, name)):
            new[name] = list(getattr(cond, name))
    new["meal_avoid"] = list(reading.meal_avoid)
    if reading.wish.strip():
        new["query"] = reading.wish
    return new
