"""Helpers of the change path in the planning graph (plan 3b-1 spec 5.2-5.5, plan 3b-2 spec
5.4-5.6): the facts a quote is matched against, the current plan as the reader and the picker
see it, the plan's carried overrides and locks, and the reply's sentences and card. The graph's
nodes (`read_follow_up`, `build_change`, `pick_dishes`, `present_change`) and `present_plan`
call these; the plan 3b-1 revise step that first used them is gone (plan 3b-2)."""

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from minemydinner.agent.card_text import NEXT_ACTION_ORDER, is_zh, unmet_for_user
from minemydinner.agent.edits import latest_model_index, locked_ids
from minemydinner.agent.revision import DishFacts
from minemydinner.models import Recipe, RecipeIngredient
from minemydinner.planning.validate import Finding, H2Override, PlanSpec

# The mark of a dish that breaks this meal's one-meal avoidance in the current plan the models
# see (plan 3b-1b spec 5.4); revise_v3 and revise_v4 named it.
BREAKS_MEAL_WISH = "BREAKS THIS MEAL'S WISH"
# The unmet code of a plan or a change that found nothing fitting the wish.
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
# A wish whose targets are empty and that guides no other new dish (spec 5.4): nothing changes
# for it, and the reply says so.
WISH_MATCHED_NOTHING_ZH = "方案里没有哪道菜需要为“{wish}”换掉，所以没有为它换菜。"
WISH_MATCHED_NOTHING_EN = "No dish in the plan matched “{wish}”, so nothing was changed for it."
# A wish whose every target is a dish the user added or kept by hand: those dishes stay.
WISH_TARGETS_LOCKED_ZH = "“{wish}”说的是你手动加上或留下的菜，这些菜先保留，没有为它换菜。"
WISH_TARGETS_LOCKED_EN = (
    "The dishes “{wish}” is about were added or kept by you, so they stay and nothing was "
    "changed for it."
)
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


def dish_facts(
    session: Session, recipe_ids: list[int]
) -> tuple[dict[int, DishFacts], dict[int, list[str]]]:
    """Per dish: the facts its quote is matched against (title; every line's name and its
    dictionary names), its dish type, and the main ingredients shown to the model (the lines marked main, else the first required lines)."""
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


def must_unmet_names(violations: list[Finding]) -> set[str]:
    """The "must use" names a set of findings misses (H7 with rule must)."""
    return {
        f.detail["name"]
        for f in violations
        if f.code == "H7" and f.detail.get("rule") == "must"
    }


def wish_unmet_actions(spec: dict) -> list[str]:
    """The next actions when nothing fits the wish (2a's style): allow shopping in
    fridge-only mode, and import recipes."""
    chosen = {"import_recipes"}
    if spec["mode"] == "fridge_only":
        chosen.add("switch_to_shopping")
    return [a for a in NEXT_ACTION_ORDER if a in chosen]


def wish_unmet_entries(locale: str) -> list[dict]:
    """The card's unmet entry when nothing in the library fits the wish."""
    if is_zh(locale):
        text = "你的菜谱库里没有找到符合这次要求的菜"
    else:
        text = "Nothing in your library fits this wish"
    return [{"code": WISH_UNMET, "recipe_id": None, "text": text}]


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


def empty_plan_question(locale: str) -> str:
    return EMPTY_PLAN_QUESTION_ZH if is_zh(locale) else EMPTY_PLAN_QUESTION_EN


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


def meal_avoid_reason(names: list[str], locale: str) -> str:
    """Why a locked dish changed for this meal's one-meal avoidance, for `locked_changed_text`
    ("这顿不吃肉")."""
    if is_zh(locale):
        return f"这顿不吃{'、'.join(names)}"
    return f"this meal leaves out {', '.join(names)}"


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
    the dishes dropped, then the dishes added ("把…换成了… / 去掉了… / 加了…")."""
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
    `title`, or None for an add; with `must_use`, the ingredient it was for is not used) or
    "removed" (a dish that breaks a rule, removed with no replacement: `title`, `why`)."""
    zh = is_zh(locale)
    kind = item["kind"]
    if kind == "missing":
        reason, name = item["reason"], item.get("title") or item["name"]
        if reason == "not_in_library":
            if zh:
                return f"菜谱库里没有{item['name']}，可以先导入菜谱。"
            return f"{item['name']} is not in your recipe library; you can import it first."
        if reason == "not_done":
            # The picker gave no usable answer: nothing is known about the library.
            if zh:
                return f"这次没能把{item['name']}加进来，可以再试一次。"
            return f"{item['name']} could not be added this time; you can try again."
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
    if kind == "unfilled" and item.get("must_use"):
        # A must-use ingredient no dish uses, and no dish that uses it was found (spec 5.4).
        names = ("、" if zh else ", ").join(item["must_use"])
        title = item.get("title")
        if title:
            if zh:
                return f"没找到用{names}做的菜来换{title}，这次没用上{names}，{title}先留着。"
            return (
                f"No dish using {names} could take the place of {title}, so {names} is not "
                f"used this time and {title} stays."
            )
        # An add that was to use it (or a choice that made no slot): nothing is added.
        word = dish_type_word(item.get("dish_type") or "", locale)
        if zh:
            text = f"没找到用{names}做的{word}，这次没用上{names}。"
            if item.get("for_count"):
                text += "也没能按你说的数量改，原来的菜都留着。"
            return text
        text = f"No {word} using {names} could be found, so {names} is not used this time."
        if item.get("for_count"):
            text += " The counts could not be changed as you asked either; the dishes you had stay."
        return text
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
    if item.get("not_done"):
        # No usable picker answer: no replacement was chosen this time, which says nothing
        # about the library.
        if zh:
            return (
                f"{item['title']}去掉了：{why or '它不符合这顿的要求'}；"
                "这次没能选出替换的菜，可以再试一次。"
            )
        return (
            f"{item['title']} was removed: {why or 'it does not fit this meal'}; no "
            "replacement could be chosen this time. You can try again."
        )
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


def wish_matched_nothing(wish: str, locale: str) -> str:
    template = WISH_MATCHED_NOTHING_ZH if is_zh(locale) else WISH_MATCHED_NOTHING_EN
    return template.format(wish=wish)


def wish_targets_locked(wish: str, locale: str) -> str:
    template = WISH_TARGETS_LOCKED_ZH if is_zh(locale) else WISH_TARGETS_LOCKED_EN
    return template.format(wish=wish)


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
