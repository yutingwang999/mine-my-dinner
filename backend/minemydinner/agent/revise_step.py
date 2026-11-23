"""Helpers of the revise step in the planning graph (plan 3b-1 spec 5.2-5.5): the facts a quote
is matched against, the current plan as the model sees it, which failed checks a locked dish
may keep as warnings, and the reply and card when a revision changes nothing. The graph's
nodes (`start_revision`, `reviser`, `check_revised`, `present_revision`) call these."""

from collections.abc import Collection

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from minemydinner.agent.card_text import CATEGORY_WORDS as COUNT_WORDS
from minemydinner.agent.card_text import (
    NEXT_ACTION_ORDER,
    detail_ranges,
    is_zh,
    next_actions,
    no_plan_reason,
    side_phrases,
    unmet_for_user,
)
from minemydinner.agent.edits import ALLOWED_CODES, latest_model_index, locked_ids
from minemydinner.agent.revision import (
    DishFacts,
    RevisionProposal,
    attempt_counts,
    changed_text,
)
from minemydinner.models import Recipe, RecipeIngredient
from minemydinner.planning.composition import (
    CATEGORY_OF_DISH_TYPE,
    SPLIT_CATEGORIES,
    count_categories,
)
from minemydinner.planning.context import MEAL_KEY
from minemydinner.planning.validate import Finding, H2Override, PlanSpec

# The category words a quote may use to point at a dish of each type ("那道汤").
CATEGORY_WORDS = {
    "meat dish": ["荤菜", "meat dish"],
    "vegetable dish": ["素菜", "vegetable dish"],
    "soup": ["汤", "soup"],
    "staple food": ["主食", "面", "饭", "粥", "粉", "staple", "noodles", "rice", "porridge"],
    "dessert": ["甜品", "甜点", "dessert"],
    "snack": ["小吃", "snack"],
}
# Words that point at the dishes that do not suit ("不适合的菜拿掉", spec 5.4): added to the
# words of every dish with an open conflict or a kept override.
CONFLICT_WORDS = ["不适合", "不合适", "不能吃", "doesn't suit", "not suitable"]
# The mark of a dish that breaks this meal's one-meal avoidance in the reviser's current plan
# (plan 3b-1b spec 5.4); revise_v3 and revise_v4 name it.
BREAKS_MEAL_WISH = "BREAKS THIS MEAL'S WISH"
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
# When the change list would leave no dish (R9, plan 3b-1b spec 5.1): plain text, no card.
EMPTY_PLAN_QUESTION_ZH = "这样就一道菜都不剩了。要重新规划一顿吗？"
EMPTY_PLAN_QUESTION_EN = "That would leave no dish. Shall I plan a new meal?"

# The change path's reply (plan 3b-2 spec 5.5, 5.6). CHANGE_PARTIAL leads the parts of a change
# that could not be done, after what was done; READING_PREFIX leads the reader's sentence on
# an ambiguous message; WHICH_DISH is the one question for a reference that matches no dish.
CHANGE_PARTIAL_ZH = "有些地方没能按你说的改："
CHANGE_PARTIAL_EN = "Some of it could not be done:"
READING_PREFIX_ZH = "我的理解："
READING_PREFIX_EN = "How I read it: "
WHICH_DISH_ZH = "你指的是哪一道菜？"
WHICH_DISH_EN = "Which dish did you mean?"
CHANGE_WISH_UNMET_ZH = "菜谱库里没有完全符合“{wish}”的菜。"
CHANGE_WISH_UNMET_EN = "Nothing in your library quite fits “{wish}”."
# The dishes stay, but the meal's conditions changed (an avoidance taken back, a new time
# limit no dish breaks): a new version carries them.
CONDITIONS_ONLY_ZH = "菜不用换，方案已经按新的要求更新。"
CONDITIONS_ONLY_EN = "No dish needed to change; the plan now follows the new conditions."
# A dish type in the user's words.
DISH_TYPE_WORDS = {
    "meat dish": ("荤菜", "meat dish"),
    "vegetable dish": ("素菜", "vegetable dish"),
    "soup": ("汤", "soup"),
    "staple food": ("主食", "staple food"),
    "dessert": ("甜品", "dessert"),
    "snack": ("小吃", "snack"),
}


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


def conflict_words(plan: dict | None) -> dict[int, list[str]]:
    """Per dish with an open conflict or a kept override: CONFLICT_WORDS plus the name of the
    ingredient, allergen, or rule text it conflicts on, so "不适合的菜" or "the egg" points at
    it, locked or not."""
    edit = (plan or {}).get("edit") or {}
    words: dict[int, list[str]] = {}
    for entry in [*edit.get("conflicts", []), *edit.get("kept", []), *edit.get("overrides", [])]:
        found = words.setdefault(entry["recipe_id"], list(CONFLICT_WORDS))
        for key in ("ingredient_name", "allergen", "text"):
            value = entry.get(key)
            # A rule text shorter than two characters would match far too broadly.
            if value and (key != "text" or len(value.strip()) >= 2):
                found.append(value)
    return {i: list(dict.fromkeys(w)) for i, w in words.items()}


def dish_facts(
    session: Session, recipe_ids: list[int], *, plan: dict | None = None
) -> tuple[dict[int, DishFacts], dict[int, list[str]]]:
    """Per dish: the facts its quote is matched against (title; every line's name and its
    dictionary names; the category words of its type, and for a dish of `plan` with an open
    conflict or a kept override the conflict words), its dish type, and the main ingredients
    shown to the model (the lines marked main, else the first required lines)."""
    if not recipe_ids:
        return {}, {}
    conflicting = conflict_words(plan)
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
            category_words=[
                *CATEGORY_WORDS.get(recipe.dish_type, []),
                *conflicting.get(recipe.id, []),
            ],
            dish_type=recipe.dish_type,
        )
        main = [line.name for line in recipe.ingredients if line.role == "main"]
        if not main:
            required = [line.name for line in recipe.ingredients if not line.optional]
            main = required[:MAX_SHOWN_INGREDIENTS]
        mains[recipe.id] = list(dict.fromkeys(main))
    return facts, mains


def current_plan_text(
    plan: dict, mains: dict[int, list[str]], avoid_ids: Collection[int] = ()
) -> str:
    """The current plan for the model, one line per dish:
    `id title (dish type; main ingredients) LOCKED|LOCKED KEPT CONFLICT|OPEN CONFLICT`, then
    BREAKS_MEAL_WISH for a dish in `avoid_ids` (it breaks this meal's one-meal avoidance). A
    dish kept by the user despite a conflict is marked so (plan 3b-1b spec 5.4), so "the
    dishes that do not suit" can name it."""
    avoided = set(avoid_ids)
    conflicted = open_conflict_ids(plan)
    locked = locked_ids(plan)
    kept = {o["recipe_id"] for o in (plan.get("edit") or {}).get("overrides", [])}
    lines = []
    for recipe in plan["recipes"]:
        recipe_id = recipe["id"]
        ingredients = ", ".join(mains.get(recipe_id, [])) or "none listed"
        line = f"- {recipe_id} {recipe['title']} ({recipe['dish_type']}; {ingredients})"
        if recipe_id in conflicted:
            line += " OPEN CONFLICT"
        elif recipe_id in kept:
            line += " LOCKED KEPT CONFLICT"
        elif recipe_id in locked:
            line += " LOCKED"
        if recipe_id in avoided:
            line += f" {BREAKS_MEAL_WISH}"
        lines.append(line)
    return "Current plan:\n" + ("\n".join(lines) or "- no dishes")


def revision_opening(
    base: str,
    plan: dict,
    mains: dict[int, list[str]],
    message: str,
    avoid_ids: Collection[int] = (),
    earlier: str | None = None,
) -> str:
    """The revise step's first message: the shortlist and the request summary (`base`, as the
    planner's opening), then the current plan (`avoid_ids` marked), then the user's
    message. On a button revision `earlier` is the wish of the turn whose card offered the
    button: it and the button's text are shown on two labelled lines, so a count button does
    not read as a second wish that contradicts the first (development run 2 of plan 3b-1b)."""
    plan_text = current_plan_text(plan, mains, avoid_ids)
    if earlier:
        said = (
            f"The user's message:\nEarlier wish: {earlier}\n"
            f"Then the user pressed: {message} (the request above already includes this change)"
        )
    else:
        said = f"The user's message: {message}"
    return f"{base}\n\n{plan_text}\n\n{said}"


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


def count_fix_hint(detail: dict, replaceable: dict[str, list[tuple[int, str]]]) -> str:
    """For the reviser's retry after an H4 finding or a type pairing problem (R7): which
    categories have too many or too few dishes, that a replacement takes the dish type of the
    dish it replaces, and (plan 3b-1b spec 5.2) the concrete dishes of each category that is
    over that may be replaced instead (`replaceable`: per category, the unlocked dishes of
    the current plan as (id, title)). Only the revise step adds it; a new plan's retry keeps
    the H4 line alone."""
    required, present = detail["required"], detail["present"]
    ranges = detail_ranges(detail)
    over: list[str] = []
    under: list[str] = []
    over_categories: list[str] = []
    side_off = False
    for category in ("meat_dishes", "vegetable_dishes"):
        low, high = ranges[category]
        have = present.get(category, 0)
        if have > high:
            over.append(_count_words(have - high, category))
            over_categories.append(category)
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
            over_categories.append(category)
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
    for category in over_categories:
        dishes = replaceable.get(category) or []
        if dishes:
            named = ", ".join(f"{title} (id {i})" for i, title in dishes)
            lines.append(f"Replace one of these {COUNT_WORDS[category][1]}: {named}.")
    return " ".join(lines)


def replaceable_dishes(plan: dict) -> dict[str, list[tuple[int, str]]]:
    """Per count category, the dishes of the plan a retry may suggest replacing: every dish
    that is not locked (a dish with an open conflict is never locked)."""
    locked = locked_ids(plan) - open_conflict_ids(plan)
    found: dict[str, list[tuple[int, str]]] = {}
    for recipe in plan["recipes"]:
        category = CATEGORY_OF_DISH_TYPE.get(recipe["dish_type"])
        if category is not None and recipe["id"] not in locked:
            found.setdefault(category, []).append((recipe["id"], recipe["title"]))
    return found


def pairing_detail(plan: dict, proposal: RevisionProposal, dish_types: dict[int, str]) -> dict:
    """For the retry after a type pairing problem (R7), an H4-shaped detail with exact sides:
    the counts the change list would give if every replaced dish (and, for a general wish,
    every removed one) took its own dish type, against the counts it gives. A category that
    is over names the dishes that may be replaced instead ("replace one of these meat
    dishes")."""
    own = {r["id"]: r["dish_type"] for r in plan["recipes"]}
    removed = {r.id: r for r in proposal.remove if r.id in own}
    expected = [own[i] for i in own if i not in removed]
    expected += [
        own[i]
        for i, item in removed.items()
        if item.mode == "replace" or proposal.target == "general"
    ]
    if proposal.target == "add":
        expected += [dish_types.get(i, "") for i in proposal.add if i not in own]
    required = count_categories(expected)
    return {
        "required": required,
        "present": attempt_counts(plan, proposal, dish_types),
        "ranges": {c: [required[c], required[c]] for c in SPLIT_CATEGORIES},
    }


def attempt_spec(
    spec: PlanSpec,
    plan: dict,
    proposal: RevisionProposal | None,
    dish_types: dict[int, str],
    counts_stated: bool,
) -> PlanSpec:
    """The spec one revision attempt is checked against (plan 3b-1b spec 5.1). When the
    message states a count (`counts_stated`) the revision's spec stands, as in 3b-1.
    Otherwise the counts are the change list's own (`attempt_counts`), as exact counts: a
    drop shrinks the plan and an add grows it, and the type pairing (R7) and add-only (R8)
    checks guard the size instead of H4. Counts outside the allowed total keep `spec`, so
    H4 reports them."""
    if proposal is None or counts_stated:
        return spec
    counts = attempt_counts(plan, proposal, dish_types)
    updates = {**counts, "meat_bound": "exact", "vegetable_bound": "exact"}
    try:
        return PlanSpec.model_validate({**spec.model_dump(), **updates})
    except ValidationError:
        return spec


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
    if code == "R7":
        if zh:
            return "换上的菜要和换掉的菜是同一类（荤菜换荤菜、素菜换素菜、汤换汤）"
        return (
            "A replacement has to be the same kind of dish as the one it replaces "
            "(meat for meat, vegetable for vegetable, soup for soup)"
        )
    if code == "R8":
        return "这次只是加菜，但没能加上合适的菜" if zh else "No fitting dish could be added"
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


def revision_actions(
    spec: dict, problem_codes: Collection[str], violation_codes: Collection[str]
) -> list[str]:
    """The next actions of a failed revision: `next_actions` over its failing checks, where a
    type pairing (R7) or add-only (R8) problem counts as a count problem (H4), since an
    attempt checked against its own counts never fails H4 itself. In fridge-only mode a
    count problem also offers to allow shopping: a revision fails on the counts there when
    nothing more can be made from what is at home ("再加一道" with nothing makeable)."""
    codes = list(violation_codes)
    if {"R7", "R8"} & set(problem_codes):
        codes.append("H4")
    chosen = set(next_actions(spec, [], codes))
    if spec["mode"] == "fridge_only" and "H4" in codes:
        chosen.add("switch_to_shopping")
    return [a for a in NEXT_ACTION_ORDER if a in chosen]


def over_limit_entries(dishes: list[dict], limit: int, locale: str) -> list[dict]:
    """Unmet entries for the asked-for dishes the time limit left out (design 5.2,
    2026-11-22), worded as an H6 finding: how long each takes against the limit."""
    return [
        {
            "code": "H6",
            "recipe_id": dish["id"],
            "text": unmet_for_user(
                {
                    "code": "H6",
                    "recipe_id": dish["id"],
                    "detail": {"time_min": dish["time_min"], "limit": limit},
                },
                {dish["id"]: dish["title"]},
                locale,
            ),
        }
        for dish in dishes
    ]


def over_limit_note(dishes: list[dict], limit: int, locale: str) -> str:
    """The reply's sentence for the asked-for dishes the time limit left out: each is in the
    library but takes longer than the limit, and dropping the limit lets it in. Never "not in
    the library"."""
    if is_zh(locale):
        parts = [
            f"{d['title']}在你的菜谱库里，但需要 {d['time_min']} 分钟，"
            f"超过了这次 {limit} 分钟的时间限制。"
            for d in dishes
        ]
        tail = "不限时间就可以用它。" if len(dishes) == 1 else "不限时间就可以用它们。"
        return "".join([*parts, tail])
    parts = [
        f"{d['title']} is in your library but takes {d['time_min']} minutes, longer than "
        f"this meal's {limit}-minute limit."
        for d in dishes
    ]
    tail = "Drop the time limit to use it." if len(dishes) == 1 else (
        "Drop the time limit to use them."
    )
    return " ".join([*parts, tail])


def with_over_limit(
    unmet: list[dict], actions: list[str], dishes: list[dict], limit: int, locale: str
) -> tuple[list[dict], list[str]]:
    """A kept plan's unmet entries and next actions when asked-for dishes were left out only by
    the time limit: their H6 entries replace the "nothing in your library fits" entry, the
    card offers drop_time_limit, and import is no longer offered (the dish is there)."""
    entries = [u for u in unmet if u["code"] != WISH_UNMET]
    entries += [e for e in over_limit_entries(dishes, limit, locale) if e not in entries]
    chosen = (set(actions) - {"import_recipes"}) | {"drop_time_limit"}
    return entries, [a for a in NEXT_ACTION_ORDER if a in chosen]


def empty_plan_question(locale: str) -> str:
    return EMPTY_PLAN_QUESTION_ZH if is_zh(locale) else EMPTY_PLAN_QUESTION_EN


def meal_only_h2(findings: list[dict]) -> bool:
    """True when there is an H2 finding and every one is this meal's one-meal avoidance."""
    h2 = [f for f in findings if f["code"] == "H2"]
    return bool(h2) and all((f.get("detail") or {}).get("diner_key") == MEAL_KEY for f in h2)


def unmet_entries(
    problems: list[dict],
    violations: list[dict],
    titles: dict[int, str],
    locale: str,
    meal_avoid: Collection[str] = (),
) -> list[dict]:
    """What stopped the last attempt, in the card's unmet shape: the revision problems, then
    the failing checks. H2 and H5 are worded without naming the dish, as on a plan that is
    not shown; an H2 that is only this meal's avoidance (`meal_avoid`, its names) says so."""
    entries = [
        {"code": p["code"], "recipe_id": p.get("recipe_id"), "text": problem_for_user(p, titles, locale)}
        for p in problems
    ]
    meal = list(meal_avoid) if meal_only_h2(violations) else []
    for finding in violations:
        if finding["code"] in ("H2", "H5"):
            text = no_plan_reason(finding["code"], locale, meal)
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


def meal_avoid_reason(names: list[str], locale: str) -> str:
    """Why a locked dish changed for this meal's one-meal avoidance, for `locked_changed_text`
    ("这顿不吃肉")."""
    if is_zh(locale):
        return f"这顿不吃{'、'.join(names)}"
    return f"this meal leaves out {', '.join(names)}"


def reply_text(
    plan: dict,
    proposal: RevisionProposal,
    card: dict,
    locale: str,
    avoid_ids: Collection[int] = (),
    meal_avoid: Collection[str] = (),
) -> str:
    """What changed, written by code from the change list (spec 5.4), then the model's note.
    For a general wish, the locked dishes it left are named too. A locked dish changed because
    it breaks this meal's avoidance (`avoid_ids`, the names in `meal_avoid`) is named with
    that reason."""
    before = {r["id"]: r["title"] for r in plan["recipes"]}
    after = {r["id"]: r["title"] for r in card["recipes"]}
    removed_ids = {r.id for r in proposal.remove}
    # A locked dish the message changed (named, in a scope, or with the conflict "the dishes
    # that do not suit" points at) gets its own sentence (plan 3b-1b spec 5.4).
    locked = locked_ids(plan) - open_conflict_ids(plan)
    pairs = replacement_pairs(plan, proposal, card)
    changed_locked = [i for i in before if i in removed_ids and i in locked]
    taken = {pairs.get(i) for i in changed_locked}
    removed = [before[i] for i in before if i in removed_ids and i not in locked]
    added = [
        after[i] for i in proposal.add if i in after and i not in before and i not in taken
    ]
    hand, kept = untouched_locked(plan, proposal, after)
    hand_added = set(plan.get("locked") or [])
    changes = [
        (before[i], after.get(pairs.get(i)), i in hand_added) for i in changed_locked
    ]
    avoided = set(avoid_ids)
    reason = meal_avoid_reason(list(meal_avoid), locale) if meal_avoid else ""
    by_wish = [c for i, c in zip(changed_locked, changes, strict=True) if i in avoided]
    by_words = [c for i, c in zip(changed_locked, changes, strict=True) if i not in avoided]
    parts = [
        changed_text(removed, added, hand, locale, kept),
        locked_changed_text(by_wish, locale, reason=reason),
        locked_changed_text(by_words, locale),
    ]
    text = ("" if is_zh(locale) else " ").join(p for p in parts if p)
    note = proposal.note.strip()
    return f"{text}\n\n{note}" if note else text


def replacement_pairs(plan: dict, proposal: RevisionProposal, card: dict) -> dict[int, int]:
    """Which added dish took each replaced dish's place, for the reply: the named replacement
    (`replace_with`), else the first added dish of the same type, else the first added dish
    left. A dropped dish has no pair."""
    own = {r["id"]: r["dish_type"] for r in plan["recipes"]}
    new = {r["id"]: r["dish_type"] for r in card["recipes"] if r["id"] not in own}
    left = [i for i in proposal.add if i in new]
    pairs: dict[int, int] = {}
    replaced = [r for r in proposal.remove if r.id in own and r.mode == "replace"]
    for item in replaced:
        if item.replace_with in left:
            pairs[item.id] = item.replace_with
            left.remove(item.replace_with)
    for item in [r for r in replaced if r.id not in pairs]:
        match = next((i for i in left if new[i] == own[item.id]), None)
        if match is None and left:
            match = left[0]
        if match is not None:
            pairs[item.id] = match
            left.remove(match)
    return pairs


def locked_changed_text(
    changes: list[tuple[str, str | None, bool]], locale: str, reason: str = ""
) -> str:
    """One sentence per locked dish the revision changed: (title, the dish that took its
    place or None for a drop, True when it was added by hand rather than kept by
    confirmation). `reason` is the why, when code knows it (for example a one-meal
    avoidance); without it the sentence says the message asked for the change."""
    zh = is_zh(locale)
    parts = []
    for title, replacement, hand in changes:
        if zh:
            who = f"{title}是你手动加的" if hand else f"{title}是你确认保留的"
            why = f"因为{reason}，" if reason else "按你这次的话"
            done = f"换成了{replacement}" if replacement else "去掉了"
            parts.append(f"{who}，{why}{done}。")
        else:
            who = f"{title} was added by you" if hand else f"You had chosen to keep {title}"
            why = f"because {reason}" if reason else "as your message asked"
            done = f"it was replaced with {replacement}" if replacement else "it was removed"
            parts.append(f"{who}; {why}, {done}.")
    return "".join(parts) if zh else " ".join(parts)


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


# ---- the change path's sentences (plan 3b-2 spec 5.5, 5.6) ----


def dish_type_word(dish_type: str, locale: str) -> str:
    words = DISH_TYPE_WORDS.get(dish_type)
    if words is None:
        return dish_type or ("菜" if is_zh(locale) else "dish")
    return words[0] if is_zh(locale) else words[1]


def change_sentences(
    pairs: list[tuple[str, str]], dropped: list[str], added: list[str], locale: str
) -> str:
    """What a change did to the dishes not locked: each replacement as (old, new) title, then
    the dishes dropped, then the dishes added, as `changed_text` words them."""
    zh = is_zh(locale)
    join = "、" if zh else ", "
    parts = []
    if pairs:
        if zh:
            parts.append("，".join(f"把{old}换成了{new}" for old, new in pairs) + "。")
        else:
            swaps = ", and ".join(f"{old} with {new}" for old, new in pairs)
            parts.append(f"Replaced {swaps}.")
    if dropped:
        parts.append(f"去掉了{join.join(dropped)}。" if zh else f"Removed {join.join(dropped)}.")
    if added:
        parts.append(f"加了{join.join(added)}。" if zh else f"Added {join.join(added)}.")
    return "".join(parts) if zh else " ".join(parts)


def rule_reason(reason: str, meal_avoid: Collection[str], locale: str) -> str:
    """Why a dish had to go, for `locked_changed_text`: this meal's avoidance or a diner's
    rule; empty for a dish the message itself pointed at."""
    if reason == "meal_avoid" and meal_avoid:
        return meal_avoid_reason(list(meal_avoid), locale)
    if reason == "diner_rule":
        return "它不适合一起吃饭的人" if is_zh(locale) else "it does not suit someone eating"
    return ""


def unfinished_sentence(item: dict, limit: int | None, meal_avoid: Collection[str], locale: str) -> str:
    """One part of a change that could not be done, in the user's words. `item["kind"]`:
    "missing" (a named dish not placed: `reason` and, for a found dish, `title`, `blocked`,
    `time_min`), "unfilled" (a slot no dish fits: `dish_type` and the replaced dish's
    `title`, or None for an add) or "removed" (a dish that breaks a rule, removed with no
    replacement: `title`, `why`)."""
    zh = is_zh(locale)
    kind = item["kind"]
    if kind == "missing":
        reason, name = item["reason"], item.get("title") or item["name"]
        if reason == "not_in_library":
            if zh:
                return f"菜谱库里没有{item['name']}，可以先导入菜谱。"
            return f"{item['name']} is not in your recipe library; you can import it first."
        if reason == "ambiguous":
            if zh:
                return f"{item['name']}在菜谱库里对得上不止一道菜，没有加上，告诉我完整的菜名就好。"
            return (
                f"{item['name']} matches more than one dish in your library, so it was not "
                "added; tell me its full title."
            )
        if reason == "over_time_limit" and limit is not None:
            dish = {"title": name, "time_min": item.get("time_min")}
            return over_limit_note([dish], limit, locale)
        blocked = item.get("blocked")
        if blocked == "stock":
            if zh:
                return f"{name}在你的菜谱库里，但家里的食材不够做它，允许购物就可以用它。"
            return f"{name} is in your library, but what is at home is not enough for it; allow shopping to use it."
        if blocked == "cookware":
            if zh:
                return f"{name}在你的菜谱库里，但需要家里没有的厨具。"
            return f"{name} is in your library, but it needs cookware you do not have."
        why = rule_reason(blocked or "", meal_avoid, locale)
        if zh:
            return f"{name}在你的菜谱库里，但{why or '它不符合这顿的要求'}，所以没有加上。"
        return f"{name} is in your library, but {why or 'it does not fit this meal'}, so it was not added."
    if kind == "unfilled":
        word = dish_type_word(item["dish_type"], locale)
        title = item.get("title")
        if title:
            if zh:
                return f"没找到合适的{word}来换{title}，先保留了它。"
            return f"No {word} fit in place of {title}, so it stays."
        if item.get("for_count"):
            # A stated count that needed this dish: no other dish was removed for it.
            if zh:
                return f"没找到可以加的{word}，所以没能按你说的数量改，原来的菜都留着。"
            return (
                f"No {word} could be added, so the counts could not be changed as you asked; "
                "the dishes you had stay."
            )
        return f"没找到可以加的{word}。" if zh else f"No {word} could be added."
    # "removed": a dish that breaks a rule leaves even with no replacement (spec 5.5).
    why = rule_reason(item.get("why") or "", meal_avoid, locale)
    if zh:
        return f"{item['title']}去掉了：{why or '它不符合这顿的要求'}，也没找到可以替换的菜。"
    return f"{item['title']} was removed: {why or 'it does not fit this meal'}, and nothing could replace it."


def reading_sentence(reading: str, locale: str) -> str:
    prefix = READING_PREFIX_ZH if is_zh(locale) else READING_PREFIX_EN
    return f"{prefix}{reading.strip()}"


def which_dish_question(reading: str, locale: str) -> str:
    """Spec 5.6: a reference that matches no dish: how it was read, then which dish."""
    question = WHICH_DISH_ZH if is_zh(locale) else WHICH_DISH_EN
    if not reading.strip():
        return question
    return reading_sentence(reading, locale) + ("" if is_zh(locale) else " ") + question


def change_wish_unmet(wish: str, locale: str) -> str:
    template = CHANGE_WISH_UNMET_ZH if is_zh(locale) else CHANGE_WISH_UNMET_EN
    return template.format(wish=wish)


def partial_text(sentences: list[str], locale: str, lead: bool) -> str:
    """The parts that could not be done, after CHANGE_PARTIAL when the change did something."""
    if not sentences:
        return ""
    zh = is_zh(locale)
    body = "".join(sentences) if zh else " ".join(sentences)
    if not lead:
        return body
    head = CHANGE_PARTIAL_ZH if zh else CHANGE_PARTIAL_EN
    return f"{head}{body}" if zh else f"{head} {body}"


def conditions_only_note(locale: str) -> str:
    return CONDITIONS_ONLY_ZH if is_zh(locale) else CONDITIONS_ONLY_EN
