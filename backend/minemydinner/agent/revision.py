"""Checks a revision proposal must pass (plan 3b-1 spec 5.2, 5.3). Pure code: no model and no
database. The revise step asks the model for a `RevisionProposal`; the code here decides whether
it respects the locks, the quotes, the change-size limit, and the plan's counts."""

import math
from collections.abc import Collection, Iterable
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
    mode: Literal["drop", "replace"] = Field(
        default="replace",
        description=(
            "What the user wants for this dish. \"drop\": the words only say not to have it "
            "(\"茄子这道不要\", \"去掉茄子\", \"drop the soup\"); the plan gets smaller and no "
            "dish takes its place. \"replace\": the words want another dish in its place "
            "(\"换 / 换成 / 换一个 / 来个 / 再要一道\", \"swap it for something else\"); add one "
            "dish of the same dish type for it, unless the user names the replacement "
            "(replace_with). A dish removed because it breaks a wish for this meal only "
            "(\"今天不想吃肉\") is replaced, never dropped."
        ),
    )
    replace_with: int | None = Field(
        default=None,
        description=(
            "Only for mode \"replace\", when the user names the dish that takes this one's place "
            "(\"用西红柿炒鸡蛋换掉鱼香肉丝\"): the recipe id of that dish. It must also be in add, "
            "and replace_with_quote must hold the user's words that name it. The named dish is "
            "used even when it is another dish type. Leave it null otherwise."
        ),
    )
    replace_with_quote: str = Field(
        default="",
        description=(
            "Only with replace_with: the user's exact words from the latest message that name "
            "the replacement dish, copied character for character. It must match that dish "
            "(part of its title or one of its ingredients). Empty otherwise."
        ),
    )


class RevisionProposal(BaseModel):
    """What the revision changes in the current plan, and why the message allows it."""

    target: Literal["named", "scope", "general", "add", "unclear"] = Field(
        description=(
            "Which dishes the latest message targets. \"named\": it points at specific dishes "
            "(\"茄子这道不要\", \"drop the soup\"); give a quote for each removed dish. "
            "\"scope\": it states a scope of dishes, such as \"replace everything\" or "
            "\"everything except the corn one\"; give scope_quote and list the dishes it "
            "leaves out in excepted. \"general\": it is a wish that points at no particular "
            "dish (\"something spicier\", \"not so many egg dishes\"); a general ingredient or "
            "taste word that does not point at one dish is a general wish, not a named dish, "
            "and so is a wish to change without saying how (\"换个思路\"); locked dishes stay. "
            "\"add\": the message only adds dishes (\"再加一道\", \"add a green vegetable\") and "
            "removes nothing: remove must be empty and add holds the new dishes. "
            "\"unclear\": only when the message points at a dish but you cannot tell which one "
            "(\"drop the soup\" with two soups); change nothing (empty remove and add) and say "
            "in note what to ask the user."
        )
    )
    remove: list[RemovedDish] = Field(
        default_factory=list,
        description="The dishes of the current plan to take out, each by its id.",
    )
    add: list[int] = Field(
        default_factory=list,
        description=(
            "The recipe ids of the dishes to add, as integers only. None of them may already "
            "be in the plan, and no id may be in both add and remove."
        ),
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

    code: Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]
    recipe_id: int | None = None
    text: str


@dataclass(frozen=True)
class DishFacts:
    """What a quote is matched against, built by the caller from the database."""

    title: str
    ingredient_names: list[str]
    category_words: list[str]
    dish_type: str = ""


def max_general_changes(unlocked: int) -> int:
    """How many unlocked dishes a general wish may replace: half, rounded up, at least one."""
    if unlocked <= 0:
        return 0
    return max(1, math.ceil(unlocked / 2))


def quote_matches(
    quote: str, title: str, ingredient_names: Iterable[str], category_words: Iterable[str]
) -> bool:
    """True when the quote contains two characters in a row of the title, an ingredient name,
    or a category word, or is one character equal to the title's last character ("面")."""
    text = quote.casefold()
    if not text.strip():
        return False
    name = title.casefold()
    if any(name[i : i + 2] in text for i in range(len(name) - 1)):
        return True
    if len(text.strip()) == 1 and text.strip() == name[-1:]:
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
    avoid_ids: Collection[int] = (),
    counts_stated: bool = False,
) -> list[RevisionProblem]:
    """The rules of spec 5.2 and 5.4. A dish with an open conflict is never locked, needs no
    quote to leave, and is outside the general wish's limit. `spec` is the revision's target
    spec (`revision_spec`): the removals a lower total needs are outside the limit too.
    `avoid_ids` are the plan's dishes that break this meal's avoidance: they count as named
    (no quote, locked allowed, outside the limit) and may be replaced by another dish type.
    `counts_stated` (the parse says the message states a count change) allows extra added
    dishes, other-type replacements and a missing replacement; the caller must pass facts for
    the added ids too (unknown-type dishes match any removal). `spec` only feeds R4's count-cut
    exemption."""
    problems: list[RevisionProblem] = []

    def add(code: str, recipe_id: int | None, text: str) -> None:
        problems.append(RevisionProblem(code=code, recipe_id=recipe_id, text=text))

    in_plan = [r["id"] for r in plan["recipes"]]
    titles = {r["id"]: r["title"] for r in plan["recipes"]}
    avoided = set(avoid_ids)
    conflicted = _open_conflict_ids(plan) | avoided
    locked = locked_ids(plan) - conflicted
    types = {r["id"]: r["dish_type"] for r in plan["recipes"]}
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

    if proposal.target == "add" and proposal.remove:
        add("R8", None, "The message only adds dishes, so remove must be empty.")
    if proposal.target == "add" and not proposal.add:
        add("R8", None, "The message adds dishes, but nothing was added.")
    for item in removed:
        if item.replace_with is None:
            continue
        if item.mode != "replace":
            add("R6", item.id, "replace_with is only for a removal with mode \"replace\".")
        elif item.replace_with not in proposal.add:
            add("R6", item.id, f"Dish {item.replace_with} is the named replacement, so it must be in add.")
        elif not item.replace_with_quote.strip():
            add("R2", item.replace_with, "The named replacement needs a quote of the words that name it.")
        elif item.replace_with_quote not in message:
            add(
                "R1",
                item.replace_with,
                f"The quote \"{item.replace_with_quote}\" does not appear in the message.",
            )
        else:
            facts = dish_facts.get(item.replace_with) or DishFacts("", [], [])
            if not quote_matches(
                item.replace_with_quote, facts.title, facts.ingredient_names, facts.category_words
            ):
                add(
                    "R2",
                    item.replace_with,
                    f"The quote \"{item.replace_with_quote}\" does not name the replacement "
                    f"dish {item.replace_with}.",
                )

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
    if proposal.target != "unclear":
        _check_pairing(proposal, removed, types, dish_facts, avoided, counts_stated, add)
    if proposal.remove and not revised_ids(plan, proposal):
        add("R9", None, "This change would leave the plan with no dish.")
    return problems


def _check_pairing(proposal, removed, types, dish_facts, avoided, counts_stated, add) -> None:
    """The size rules (R7). A general wish never drops (R7). Every replace-removal needs one
    added dish: matched by dish type; a dish named by `replace_with` and a replacement of an
    avoided dish may be another type; so may any replacement when the message states a count
    (`counts_stated`). An added dish whose type is unknown here (no facts yet) matches any
    removal, so this never misfires before the caller supplies facts for the added ids. A
    replace-removal left without an added dish is a missing replacement, and an added dish left
    over is an extra; both are allowed only when counts are stated (extras also under target
    add)."""
    if proposal.target == "general":
        for item in removed:
            if item.mode == "drop":
                add(
                    "R7",
                    item.id,
                    f"A general wish never drops a dish: replace dish {item.id} with one of the "
                    "same dish type.",
                )
    added = [i for i in dict.fromkeys(proposal.add) if i not in types]
    kind = {i: (dish_facts[i].dish_type if i in dish_facts else "") for i in added}
    pending = [r for r in removed if r.mode == "replace"]

    def take(item, adds) -> bool:
        pending.remove(item)
        added.remove(adds)
        return True

    for item in list(pending):
        if item.replace_with in added:
            take(item, item.replace_with)
    ordered = sorted(pending, key=lambda r: r.id in avoided)
    for item in ordered:
        same = next((i for i in added if kind[i] == types[item.id]), None)
        if same is not None:
            take(item, same)
    for item in [r for r in ordered if r in pending]:
        unknown = next((i for i in added if not kind[i]), None)
        if unknown is not None:
            take(item, unknown)
    for item in [r for r in ordered if r in pending and r.id in avoided]:
        if added:
            take(item, added[0])
    for item in [r for r in ordered if r in pending]:
        if added:
            other = added[0]
            take(item, other)
            if not counts_stated:
                add(
                    "R7",
                    item.id,
                    f"Dish {item.id} is a {types[item.id]}, so its replacement must be a "
                    f"{types[item.id]} too, unless the user names the replacement dish or the "
                    "message changes a count.",
                )
        elif not counts_stated:
            add("R7", item.id, f"Dish {item.id} is replaced but no dish is added in its place.")
    if added and proposal.target != "add" and not counts_stated:
        add(
            "R7",
            added[0],
            f"Dish {added[0]} replaces no removed dish; add extra dishes only when the message "
            "asks for more dishes.",
        )


def attempt_counts(
    plan: dict, proposal: RevisionProposal, dish_types: dict[int, str]
) -> dict[str, int]:
    """The category counts of the revised plan: `count_categories` over the dish types of
    `revised_ids` (a dish's type from `dish_types`, else from the plan)."""
    own = {r["id"]: r["dish_type"] for r in plan["recipes"]}
    return count_categories(
        dish_types.get(i) or own.get(i, "") for i in revised_ids(plan, proposal)
    )


_REPLACED_FIELDS = ("mode", "time_limit_min", "must_use", "prefer_use", "all_use")


def _stated(request: dict, previous: dict | None, name: str) -> bool:
    """The request states the field and it differs from the previous request. A null or an
    empty list is not a statement, so it never clears a condition."""
    value = request.get(name)
    if value is None or value == []:
        return False
    return value != (previous or {}).get(name)


def _changed_counts(request: dict, previous: dict | None) -> set[str]:
    """The count fields the newest message sets. From parse_v6 the parse says whether the
    message states a count (`counts_stated_now`): if it does, every non-null total and
    category count is taken, even one equal to the previous request (a hand-trimmed plan of
    2, asked as 3, with "再加一道" is 3), but a meat or vegetable side only when it differs
    from the previous request, so a split carried over from an earlier message never becomes a
    stale exact side; if not, none is. A request parsed before the field has it null: then a
    count is taken only when it differs from the previous request."""
    stated_now = request.get("counts_stated_now")
    if stated_now is None:
        return {n for n in COUNT_FIELDS if _stated(request, previous, n)}
    if not stated_now:
        return set()
    return {
        n
        for n in COUNT_FIELDS
        if (_stated(request, previous, n) if n in SPLIT_CATEGORIES else request.get(n) is not None)
    }


def counts_changed(request: dict, previous: dict | None) -> bool:
    """True when the newest message states a count change (the `counts_stated` of
    `check_revision`)."""
    return bool(_changed_counts(request, previous))


def revision_spec(plan: dict, previous_request: dict | None, request: dict) -> PlanSpec:
    """The spec of a revision (spec 5.2, 5.3). The counts are the plan's own, as exact counts,
    so a hand-grown plan keeps its size; a count the message changes replaces its field
    (`_changed_counts`). A plan with no dishes keeps the spec's counts. Other fields change only
    when the request states them anew."""
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
    changed = _changed_counts(request, previous_request)
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
