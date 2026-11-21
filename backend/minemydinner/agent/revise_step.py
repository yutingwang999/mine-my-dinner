"""Helpers of the revise step in the planning graph (plan 3b-1 spec 5.2-5.5): the facts a quote
is matched against, the current plan as the model sees it, which failed checks a locked dish
may keep as warnings, and the reply and card when a revision changes nothing. The graph's
nodes (`start_revision`, `reviser`, `check_revised`, `present_revision`) call these."""

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from minemydinner.agent.card_text import CATEGORY_WORDS as COUNT_WORDS
from minemydinner.agent.card_text import (
    NEXT_ACTION_ORDER,
    detail_ranges,
    is_zh,
    no_plan_reason,
    side_phrases,
    unmet_for_user,
)
from minemydinner.agent.edits import ALLOWED_CODES, latest_model_index, locked_ids
from minemydinner.agent.revision import DishFacts, RevisionProposal, changed_text
from minemydinner.models import Recipe, RecipeIngredient
from minemydinner.planning.validate import Finding, H2Override, PlanSpec

# The category words a quote may use to point at a dish of each type ("那道汤").
CATEGORY_WORDS = {
    "meat dish": ["荤菜", "meat dish"],
    "vegetable dish": ["素菜", "vegetable dish"],
    "soup": ["汤", "soup"],
    "staple food": ["主食", "staple"],
    "dessert": ["甜品", "甜点", "dessert"],
    "snack": ["小吃", "snack"],
}
# The unmet code of a revision that found nothing fitting the wish.
WISH_UNMET = "wish_unmet"
# Main ingredients shown per dish when a recipe marks none as main.
MAX_SHOWN_INGREDIENTS = 3

FAILED_NOTE_EN = (
    "I could not change the plan as asked, so it stays as it was. "
    "Below is why and what you can change."
)
FAILED_NOTE_ZH = "没能按这次的要求改好方案，所以方案保持原样。下面是原因和可以调整的地方。"
FAILED_NOTE_BARE_EN = "I could not change the plan as asked, so it stays as it was. Below is why."
FAILED_NOTE_BARE_ZH = "没能按这次的要求改好方案，所以方案保持原样。下面是原因。"
# When the model changes nothing and gives no note of its own.
UNCHANGED_NOTE_EN = "I found nothing to change for that, so the plan stays as it was."
UNCHANGED_NOTE_ZH = "这次没有找到可以改的地方，方案保持原样。"


def from_version(current: dict, versions: list[dict]) -> int:
    """The index in `plan_versions` of the version the current plan is. A manual version is
    always the last entry (later edits replace it in place); a restored or revised card names
    its model version in `model_version`; a plan made from scratch carries no index and is the
    latest model version."""
    if current.get("edited"):
        return len(versions) - 1
    if current.get("model_version") is not None:
        return current["model_version"]
    return latest_model_index(versions)


def open_conflict_ids(plan: dict) -> set[int]:
    """Dishes with a conflict the user has not confirmed (red on the card)."""
    return {c["recipe_id"] for c in (plan.get("edit") or {}).get("conflicts", [])}


def dish_facts(
    session: Session, recipe_ids: list[int]
) -> tuple[dict[int, DishFacts], dict[int, list[str]]]:
    """Per dish: the facts its quote is matched against (title; every line's name and its
    dictionary names; the category words of its type), and the main ingredients shown to the
    model (the lines marked main, else the first required lines)."""
    if not recipe_ids:
        return {}, {}
    recipes = session.scalars(
        select(Recipe)
        .where(Recipe.id.in_(recipe_ids))
        .options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.ingredient))
    ).all()
    facts: dict[int, DishFacts] = {}
    mains: dict[int, list[str]] = {}
    for recipe in recipes:
        names: list[str] = []
        for line in recipe.ingredients:
            names.append(line.name)
            if line.ingredient is not None:
                names += [n for n in (line.ingredient.name_zh, line.ingredient.name_en) if n]
        facts[recipe.id] = DishFacts(
            title=recipe.title,
            ingredient_names=list(dict.fromkeys(names)),
            category_words=list(CATEGORY_WORDS.get(recipe.dish_type, [])),
        )
        main = [line.name for line in recipe.ingredients if line.role == "main"]
        if not main:
            required = [line.name for line in recipe.ingredients if not line.optional]
            main = required[:MAX_SHOWN_INGREDIENTS]
        mains[recipe.id] = list(dict.fromkeys(main))
    return facts, mains


def current_plan_text(plan: dict, mains: dict[int, list[str]]) -> str:
    """The current plan for the model, one line per dish:
    `id title (dish type; main ingredients) LOCKED|OPEN CONFLICT`."""
    conflicted = open_conflict_ids(plan)
    locked = locked_ids(plan)
    lines = []
    for recipe in plan["recipes"]:
        recipe_id = recipe["id"]
        ingredients = ", ".join(mains.get(recipe_id, [])) or "none listed"
        line = f"- {recipe_id} {recipe['title']} ({recipe['dish_type']}; {ingredients})"
        if recipe_id in conflicted:
            line += " OPEN CONFLICT"
        elif recipe_id in locked:
            line += " LOCKED"
        lines.append(line)
    return "Current plan:\n" + ("\n".join(lines) or "- no dishes")


def revision_opening(base: str, plan: dict, mains: dict[int, list[str]], message: str) -> str:
    """The revise step's first message: the shortlist and the request summary (`base`, as the
    planner's opening), then the current plan, then the user's message."""
    return f"{base}\n\n{current_plan_text(plan, mains)}\n\nThe user's message: {message}"


def carried_overrides(plan: dict, recipe_ids: list[int]) -> list[H2Override]:
    """The plan's overrides of dishes still in the revised plan; a removed dish's are dropped."""
    kept = set(recipe_ids)
    return [
        H2Override(**o)
        for o in (plan.get("edit") or {}).get("overrides", [])
        if o["recipe_id"] in kept
    ]


def kept_locked(plan: dict, recipe_ids: list[int]) -> list[int]:
    """The dishes added by hand that the revised plan still has. Override dishes stay locked
    through their carried overrides."""
    kept = set(recipe_ids)
    return [i for i in plan.get("locked") or [] if i in kept]


def split_violations(
    violations: list[Finding], locked: set[int], must_unmet_before: Collection[str] = ()
) -> tuple[list[Finding], list[Finding]]:
    """(failing, allowed): H1, H3, H6 and H7 caused only by locked dishes are allowed, as on a
    manual version (3a ALLOWED_CODES), since the revision may not remove them. A "must use"
    H7 points at no dish: it is allowed only when the current plan already missed that name
    (`must_unmet_before`, for example after a hand edit removed the dish that used it), so a
    revision is not failed for what it did not cause. Every other finding fails, H2
    included."""
    failing, allowed = [], []
    for finding in violations:
        if finding.code in ("H1", "H3", "H6"):
            caused = {finding.recipe_id}
        elif finding.code == "H7" and finding.detail.get("rule") == "all":
            caused = set(finding.detail.get("recipes_without") or [])
        else:
            caused = set()
        must_before = (
            finding.code == "H7"
            and finding.detail.get("rule") == "must"
            and finding.detail.get("name") in must_unmet_before
        )
        if must_before or (finding.code in ALLOWED_CODES and caused and caused <= locked):
            allowed.append(finding)
        else:
            failing.append(finding)
    return failing, allowed


def must_unmet_names(violations: list[Finding]) -> set[str]:
    """The "must use" names a set of findings misses (H7 with rule must)."""
    return {
        f.detail["name"]
        for f in violations
        if f.code == "H7" and f.detail.get("rule") == "must"
    }


def _count_words(n: int, category: str) -> str:
    one, many, _ = COUNT_WORDS[category]
    return f"{n} {one if n == 1 else many}"


def count_fix_hint(detail: dict) -> str:
    """For the reviser's retry after an H4 finding: which categories have too many or too few
    dishes, and that a replacement takes the dish type of the dish it replaces (spec 5.2: a
    revision keeps the plan's counts and categories unless the message changes a count).
    Only the revise step adds it; a new plan's retry keeps the H4 line alone."""
    required, present = detail["required"], detail["present"]
    ranges = detail_ranges(detail)
    over: list[str] = []
    under: list[str] = []
    side_off = False
    for category in ("meat_dishes", "vegetable_dishes"):
        low, high = ranges[category]
        have = present.get(category, 0)
        if have > high:
            over.append(_count_words(have - high, category))
            side_off = True
        elif have < low:
            under.append(_count_words(low - have, category))
            side_off = True
    gap = present.get("dishes", 0) - required.get("dishes", 0)
    if gap and not side_off:
        words = f"{_count_words(abs(gap), 'dishes')} (meat or vegetable)"
        (over if gap > 0 else under).append(words)
    for category in ("soups", "staples", "desserts", "snacks"):
        diff = present.get(category, 0) - required.get(category, 0)
        if diff > 0:
            over.append(_count_words(diff, category))
        elif diff < 0:
            under.append(_count_words(-diff, category))
    total = required.get("dishes", 0)
    sides = side_phrases(ranges, total, False)
    keep = _count_words(total, "dishes")
    if sides:
        keep += ": " + " and ".join(sides.values())
    lines = [f"The revised plan must have {keep}."]
    if over:
        lines.append(f"Too many: {', '.join(over)}.")
    if under:
        lines.append(f"Too few: {', '.join(under)}.")
    lines.append(
        "Replace each removed dish with one of the same dish type (a meat dish for a meat "
        "dish, a vegetable dish for a vegetable dish, a soup for a soup) unless the message "
        "changes a count, and choose the dish to remove from the category the wish fits."
    )
    return " ".join(lines)


def problem_for_user(problem: dict, titles: dict[int, str], locale: str) -> str:
    """A revision problem (R1-R6) in the user's words."""
    zh = is_zh(locale)
    title = titles.get(problem.get("recipe_id"), "")
    code = problem["code"]
    if code in ("R1", "R2"):
        if title:
            return f"没能确定你的话指的是{title}" if zh else f"I could not tell that you meant {title}"
        return "没能确定你的话指的是哪些菜" if zh else "I could not tell which dishes you meant"
    if code == "R3":
        if zh:
            return f"{title}是你手动加的或确认保留的，这次的要求没有点到它，所以不能换掉"
        return f"{title} was added or kept by you and your message does not name it, so it stays"
    if code == "R4":
        if zh:
            return "这样的要求最多只能换掉一半的菜"
        return "A wish like this may change at most half of the dishes"
    if code == "R5":
        return f"你说过要保留{title}" if zh else f"You asked to keep {title}"
    if code == WISH_UNMET:
        if zh:
            return "你的菜谱库里没有找到符合这次要求的菜"
        return "Nothing in your library fits this wish"
    return "没能得到有效的修改" if zh else "No valid change could be made"


def wish_unmet_actions(spec: dict) -> list[str]:
    """The next actions when nothing fits the wish (2a's style): allow shopping in
    fridge-only mode, and import recipes."""
    chosen = {"import_recipes"}
    if spec["mode"] == "fridge_only":
        chosen.add("switch_to_shopping")
    return [a for a in NEXT_ACTION_ORDER if a in chosen]


def unmet_entries(
    problems: list[dict], violations: list[dict], titles: dict[int, str], locale: str
) -> list[dict]:
    """What stopped the last attempt, in the card's unmet shape: the revision problems, then
    the failing checks. H2 and H5 are worded without naming the dish, as on a plan that is
    not shown."""
    entries = [
        {"code": p["code"], "recipe_id": p.get("recipe_id"), "text": problem_for_user(p, titles, locale)}
        for p in problems
    ]
    for finding in violations:
        if finding["code"] in ("H2", "H5"):
            text = no_plan_reason(finding["code"], locale)
        else:
            text = unmet_for_user(finding, titles, locale)
        entries.append({"code": finding["code"], "recipe_id": finding["recipe_id"], "text": text})
    unique: list[dict] = []
    for entry in entries:
        if entry not in unique:
            unique.append(entry)
    return unique


def failed_note(locale: str, has_actions: bool) -> str:
    if is_zh(locale):
        return FAILED_NOTE_ZH if has_actions else FAILED_NOTE_BARE_ZH
    return FAILED_NOTE_EN if has_actions else FAILED_NOTE_BARE_EN


def unchanged_note(locale: str) -> str:
    return UNCHANGED_NOTE_ZH if is_zh(locale) else UNCHANGED_NOTE_EN


def revision_defaults(defaults_used: list[str], old_spec: dict, new_spec: PlanSpec) -> list[str]:
    """The plan's defaults that still hold: a field the revision changed is no longer a
    default."""
    new = new_spec.model_dump()
    return [
        name
        for name in defaults_used
        if name not in new or new[name] == old_spec.get(name)
    ]


def untouched_locked(
    plan: dict, proposal: RevisionProposal, after: dict[int, str]
) -> tuple[list[str], list[str]]:
    """The locked dishes a general wish left (spec 5.2), as (added by hand, kept by the user's
    confirmation) titles. Only when the wish removed something or changed nothing: an add-only
    turn ("再加一道吧") touched nothing it could have. A wish that names or scopes dishes left
    the others by the user's own words, so none are listed."""
    if proposal.target != "general" or (proposal.add and not proposal.remove):
        return [], []
    conflicted = open_conflict_ids(plan)
    hand = [i for i in kept_locked(plan, list(after)) if i not in conflicted]
    kept = [
        i
        for i in after
        if i in locked_ids(plan) and i not in hand and i not in conflicted
    ]
    return [after[i] for i in hand], [after[i] for i in kept]


def reply_text(
    plan: dict,
    proposal: RevisionProposal,
    card: dict,
    locale: str,
) -> str:
    """What changed, written by code from the change list (spec 5.4), then the model's note.
    For a general wish, the locked dishes it left are named too."""
    before = {r["id"]: r["title"] for r in plan["recipes"]}
    after = {r["id"]: r["title"] for r in card["recipes"]}
    removed_ids = {r.id for r in proposal.remove}
    removed = [before[i] for i in before if i in removed_ids]
    added = [after[i] for i in proposal.add if i in after and i not in before]
    hand, kept = untouched_locked(plan, proposal, after)
    text = changed_text(removed, added, hand, locale, kept)
    note = proposal.note.strip()
    return f"{text}\n\n{note}" if note else text


def unchanged_card(current: dict, unmet: list[dict], actions: list[str]) -> dict:
    """The reply card of a revision that changed nothing: the current plan's dishes and fields,
    not passed, with what stopped it."""
    return {**current, "passed": False, "unmet": unmet, "next_actions": actions}


def without_deleted(plan: dict, deleted: Collection[int]) -> dict:
    """The plan a revision works on: the current plan without the dishes that left the
    library since it was shown. They leave quietly, as on a manual edit (3a `_check`)."""
    if not deleted:
        return plan
    gone = set(deleted)
    return {**plan, "recipes": [r for r in plan["recipes"] if r["id"] not in gone]}


def deleted_note(plan: dict, deleted: Collection[int]) -> list[dict]:
    """The card's quiet note for the dishes `without_deleted` dropped, or none."""
    gone = set(deleted)
    titles = [r["title"] for r in plan["recipes"] if r["id"] in gone]
    return [{"field": "deleted_recipes", "titles": titles}] if titles else []


def card_search_notes(card: dict) -> dict:
    """A card's search notes in the state's `search_notes` shape."""
    return {
        "left_out_unknown_allergens": list(card.get("left_out_unknown_allergens") or []),
        "left_out_details": list(card.get("left_out_details") or []),
        "semantic_paused": bool(card.get("semantic_paused", False)),
    }
