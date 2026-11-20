"""Checks a revision proposal must pass (plan 3b-1 spec 5.2, 5.3). Pure code: no model and no
database. The revise step asks the model for a `RevisionProposal`; the code here decides whether
it respects the locks, the quotes, the change-size limit, and the plan's counts."""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from minemydinner.agent.card_text import is_zh
from minemydinner.agent.edits import locked_ids
from minemydinner.agent.request import COUNT_FIELDS
from minemydinner.planning.composition import SPLIT_CATEGORIES, count_categories
from minemydinner.planning.validate import PlanSpec


class RemovedDish(BaseModel):
    """One dish of the current plan that the revision takes out."""

    id: int = Field(description="The id of the dish in the current plan.")
    quote: str = Field(
        default="",
        description=(
            "Only when the target is \"named\": the user's exact words from the latest message "
            "that point at THIS dish, copied character for character (for example the words "
            "\"茄子这道\" or \"that soup\"). It must appear in the message and match the dish: "
            "part of its title, one of its ingredients, or its category word such as soup. "
            "Leave it empty for any other target, and for a dish that is removed only because "
            "it has an open conflict."
        ),
    )


class RevisionProposal(BaseModel):
    """What the revision changes in the current plan, and why the message allows it."""

    target: Literal["named", "scope", "general", "unclear"] = Field(
        description=(
            "Which dishes the latest message targets. \"named\": it points at specific dishes "
            "(\"茄子这道不要\", \"drop the soup\"); give a quote for each removed dish. "
            "\"scope\": it states a scope of dishes, such as \"replace everything\" or "
            "\"everything except the corn one\"; give scope_quote and list the dishes it "
            "leaves out in excepted. \"general\": it is a wish that points at no particular "
            "dish (\"something spicier\", \"not so many egg dishes\"); a general ingredient or "
            "taste word that does not point at one dish is a general wish, not a named dish, "
            "and locked dishes stay. \"unclear\": you cannot tell which dishes are meant; "
            "change nothing (empty remove and add) and say in note what to ask the user."
        )
    )
    remove: list[RemovedDish] = Field(
        default_factory=list,
        description="The dishes of the current plan to take out, each by its id.",
    )
    add: list[int] = Field(
        default_factory=list,
        description="The ids of the dishes to add. None of them may already be in the plan.",
    )
    scope_quote: str = Field(
        default="",
        description=(
            "Only for target \"scope\": the user's exact words that state the scope and its "
            "exceptions, copied from the latest message. Empty for every other target."
        ),
    )
    excepted: list[int] = Field(
        default_factory=list,
        description=(
            "Only for target \"scope\": the ids of the dishes the scope leaves out (\"except "
            "the corn one\"). They must stay in the plan."
        ),
    )
    note: str = Field(
        description=(
            "One or two sentences for the user, in the user's language, on what you changed "
            "and why, or, for \"unclear\", the question that asks which dish is meant."
        )
    )


def asks_back(proposal: RevisionProposal | dict) -> bool:
    """An unclear target with no change: the model asks which dish is meant. Takes the
    proposal or its dump (as the graph state keeps it), so the graph and the evaluation share
    one rule."""
    data = proposal.model_dump() if isinstance(proposal, RevisionProposal) else proposal
    return data.get("target") == "unclear" and not data.get("remove") and not data.get("add")


class RevisionProblem(BaseModel):
    """A way the proposal breaks the rules; `text` is English and goes into the retry message."""

    code: Literal["R1", "R2", "R3", "R4", "R5", "R6"]
    recipe_id: int | None = None
    text: str


@dataclass(frozen=True)
class DishFacts:
    """What a quote is matched against, built by the caller from the database."""

    title: str
    ingredient_names: list[str]
    category_words: list[str]


def max_general_changes(unlocked: int) -> int:
    """How many unlocked dishes a general wish may replace: half, rounded up, at least one."""
    if unlocked <= 0:
        return 0
    return max(1, math.ceil(unlocked / 2))


def quote_matches(
    quote: str, title: str, ingredient_names: Iterable[str], category_words: Iterable[str]
) -> bool:
    """True when the quote contains two characters in a row of the title, an ingredient name,
    or a category word."""
    text = quote.casefold()
    if not text.strip():
        return False
    name = title.casefold()
    if any(name[i : i + 2] in text for i in range(len(name) - 1)):
        return True
    words = [*ingredient_names, *category_words]
    return any(word.strip() and word.casefold() in text for word in words)


def _open_conflict_ids(plan: dict) -> set[int]:
    return {c["recipe_id"] for c in (plan.get("edit") or {}).get("conflicts", [])}


def _total(counts: dict) -> int:
    return sum(counts[n] for n in ("dishes", "soups", "staples", "desserts", "snacks"))


def check_revision(
    plan: dict,
    proposal: RevisionProposal,
    message: str,
    dish_facts: dict[int, DishFacts],
    *,
    spec: PlanSpec | None = None,
) -> list[RevisionProblem]:
    """The rules of spec 5.2. A dish with an open conflict is never locked, needs no quote to
    leave, and is outside the general wish's limit. `spec` is the revision's target spec
    (`revision_spec`): the removals a lower total needs are outside the limit too."""
    problems: list[RevisionProblem] = []

    def add(code: str, recipe_id: int | None, text: str) -> None:
        problems.append(RevisionProblem(code=code, recipe_id=recipe_id, text=text))

    in_plan = [r["id"] for r in plan["recipes"]]
    titles = {r["id"]: r["title"] for r in plan["recipes"]}
    conflicted = _open_conflict_ids(plan)
    locked = locked_ids(plan) - conflicted
    removed = [r for r in proposal.remove if r.id in titles]

    if proposal.target == "unclear" and (proposal.remove or proposal.add):
        add("R6", None, "The target is unclear, so remove and add must both be empty.")
    for name, ids in (("remove", [r.id for r in proposal.remove]), ("add", proposal.add)):
        for recipe_id in sorted({i for i in ids if ids.count(i) > 1}):
            add("R6", recipe_id, f"Dish {recipe_id} is listed more than once in {name}.")
    for item in proposal.remove:
        if item.id not in titles:
            add("R6", item.id, f"Dish {item.id} is not in the current plan, so it cannot be removed.")
    for recipe_id in proposal.add:
        if recipe_id in titles:
            add("R6", recipe_id, f"Dish {recipe_id} is already in the plan, so it cannot be added.")

    if proposal.target == "named":
        for item in removed:
            if item.id in conflicted:
                continue
            title = titles[item.id]
            if not item.quote.strip():
                add("R2", item.id, f"Removing \"{title}\" needs a quote of the words that name it.")
            elif item.quote not in message:
                add("R1", item.id, f"The quote \"{item.quote}\" does not appear in the message.")
            else:
                facts = dish_facts.get(item.id) or DishFacts(title, [], [])
                if not quote_matches(
                    item.quote, facts.title, facts.ingredient_names, facts.category_words
                ):
                    add(
                        "R2",
                        item.id,
                        f"The quote \"{item.quote}\" does not name \"{title}\": it matches none of "
                        "its title, ingredients, or category word.",
                    )
    elif proposal.target == "scope":
        if not proposal.scope_quote.strip() or proposal.scope_quote not in message:
            add("R1", None, "The scope quote is empty or does not appear in the message.")
        for item in removed:
            if item.id in proposal.excepted and item.id not in conflicted:
                add(
                    "R5",
                    item.id,
                    f"\"{titles[item.id]}\" is excepted by the scope, so it must stay.",
                )
    elif proposal.target == "general":
        counted = 0
        for item in removed:
            if item.id in conflicted:
                continue
            if item.id in locked:
                add(
                    "R3",
                    item.id,
                    f"\"{titles[item.id]}\" is locked and the message does not name it; a "
                    "general wish never removes a locked dish.",
                )
            else:
                counted += 1
        unlocked = len([i for i in in_plan if i not in locked])
        exempt = 0
        if spec is not None:
            current = _total(count_categories(r["dish_type"] for r in plan["recipes"]))
            exempt = max(0, current - _total(spec.model_dump()))
        limit = max_general_changes(unlocked) + exempt
        if counted > limit:
            add(
                "R4",
                None,
                f"A general wish may remove at most {limit} of the {unlocked} unlocked dishes "
                f"(half, rounded up, plus {exempt} that a lower dish count needs). Removals of "
                f"dishes with an open conflict are not counted; this removes {counted} other "
                "dishes.",
            )
    return problems


_REPLACED_FIELDS = ("mode", "time_limit_min", "must_use", "prefer_use", "all_use")


def _stated(request: dict, previous: dict | None, name: str) -> bool:
    """The request states the field and it differs from the previous request. A null or an
    empty list is not a statement, so it never clears a condition."""
    value = request.get(name)
    if value is None or value == []:
        return False
    return value != (previous or {}).get(name)


def revision_spec(plan: dict, previous_request: dict | None, request: dict) -> PlanSpec:
    """The spec of a revision (spec 5.2, 5.3). The counts are the plan's own, as exact counts,
    so a hand-grown plan keeps its size; a count the message changes replaces its field. A plan
    with no dishes keeps the spec's counts. Other fields change only when the request states
    them anew."""
    spec = PlanSpec.model_validate(plan["spec"])
    updates: dict = {}
    if plan["recipes"]:
        present = count_categories(r["dish_type"] for r in plan["recipes"])
        updates.update(
            dishes=present["dishes"],
            soups=present["soups"],
            meat_dishes=present["meat_dishes"],
            vegetable_dishes=present["vegetable_dishes"],
            meat_bound="exact",
            vegetable_bound="exact",
            staples=present["staples"],
            desserts=present["desserts"],
            snacks=present["snacks"],
        )
    changed = {n for n in COUNT_FIELDS if _stated(request, previous_request, n)}
    for name in ("soups", "staples", "desserts", "snacks"):
        if name in changed:
            updates[name] = request[name]
    if "dishes" in changed:
        updates["dishes"] = request["dishes"]
    # A new total makes the old split meaningless, and a changed side leaves the other side
    # free: only a side the request itself changes carries a count. The total stays the plan's
    # unless the request changes it.
    if changed & {"dishes", *SPLIT_CATEGORIES}:
        for name in SPLIT_CATEGORIES:
            bound_name = name.split("_")[0] + "_bound"
            if name in changed:
                updates[name] = request[name]
                updates[bound_name] = request.get(bound_name) or "exact"
            else:
                updates[name] = None
                updates[bound_name] = None
    for name in _REPLACED_FIELDS:
        if _stated(request, previous_request, name):
            updates[name] = request[name]
    return PlanSpec.model_validate({**spec.model_dump(), **updates})


def revised_ids(plan: dict, proposal: RevisionProposal) -> list[int]:
    """The plan's ids in order without the removed ones, then the added ones."""
    removed = {r.id for r in proposal.remove}
    kept = [r["id"] for r in plan["recipes"] if r["id"] not in removed]
    return [*kept, *(i for i in proposal.add if i not in kept)]


def changed_text(
    removed: list[str],
    added: list[str],
    untouched_locked: list[str],
    locale: str,
    untouched_kept: list[str] = (),
) -> str:
    """The sentence on what the revision changed, and which locked dishes it left: those added
    by hand (`untouched_locked`) and those the user chose to keep (`untouched_kept`)."""
    zh = is_zh(locale)
    join = "、" if zh else ", "
    parts = []
    if len(removed) == 1 and len(added) == 1:
        parts.append(
            f"把{removed[0]}换成了{added[0]}。" if zh else f"Replaced {removed[0]} with {added[0]}."
        )
    else:
        if removed:
            parts.append(f"去掉了{join.join(removed)}。" if zh else f"Removed {join.join(removed)}.")
        if added:
            parts.append(f"加了{join.join(added)}。" if zh else f"Added {join.join(added)}.")
    if untouched_locked:
        names = join.join(untouched_locked)
        parts.append(
            f"{names}是你手动加的，没有改动。"
            if zh
            else f"{names} was added by you, so it was left as is."
        )
    if untouched_kept:
        names = join.join(untouched_kept)
        parts.append(
            f"{names}是你确认保留的，没有改动。"
            if zh
            else f"You chose to keep {names}, so it was left as is."
        )
    return "".join(parts) if zh else " ".join(parts)
