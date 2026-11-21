"""Fixed card wording: plain words for counts, unmet rules, feasibility reasons and next
actions, in the request's locale. Never model output."""

from collections.abc import Sequence

from minemydinner.planning.composition import ranges_for


def is_zh(locale: str) -> bool:
    return locale == "zh"


CATEGORY_WORDS = {
    "dishes": ("dish", "dishes", "道菜"),
    "soups": ("soup", "soups", "道汤"),
    "meat_dishes": ("meat dish", "meat dishes", "道荤菜"),
    "vegetable_dishes": ("vegetable dish", "vegetable dishes", "道素菜"),
    "staples": ("staple food", "staple foods", "份主食"),
    "desserts": ("dessert", "desserts", "份甜品"),
    "snacks": ("snack", "snacks", "份小吃"),
}


def side_phrase(low: int, high: int, total: int, category: str, zh: bool) -> str | None:
    """One meat or vegetable side in words, from its range against the dish total (design
    5.11): "6 vegetable dishes", "2 to 3 meat dishes", "at least 2 meat dishes", "at most 1
    vegetable dish", or None when the side is not constrained."""
    one, many, cn = CATEGORY_WORDS[category]

    def en(n: int) -> str:
        return f"{n} {one if n == 1 else many}"

    if total <= 0:
        return None
    if low == high:
        return f"{low} {cn}" if zh else en(low)
    if low > 0 and high < total:
        return f"{low} 到 {high} {cn}" if zh else f"{low} to {high} {many}"
    if low > 0:
        return f"至少 {low} {cn}" if zh else f"at least {en(low)}"
    if high < total:
        return f"最多 {high} {cn}" if zh else f"at most {en(high)}"
    return None


def side_phrases(ranges: dict, total: int, zh: bool) -> dict[str, str]:
    """The phrase of each constrained side, in meat-then-vegetable order."""
    phrases = {}
    for side in SIDE_CATEGORIES:
        low, high = ranges[side]
        phrase = side_phrase(low, high, total, side, zh)
        if phrase is not None:
            phrases[side] = phrase
    return phrases


def detail_ranges(detail: dict) -> dict[str, tuple[int, int]]:
    """The side ranges of an H4 finding. A finding saved before ranges existed carries the
    named sides in `required` and a `minimum` flag instead."""
    if "ranges" in detail:
        return {side: (low, high) for side, (low, high) in detail["ranges"].items()}
    required = detail["required"]
    bound = "at_least" if detail.get("minimum") else "exact"
    return ranges_for(
        required.get("dishes", 0),
        required.get("meat_dishes"),
        bound,
        required.get("vegetable_dishes"),
        bound,
    )


def counts_text(detail: dict, zh: bool, wanted: bool = False) -> str:
    """The counts of an H4 finding in plain words. Without `wanted`, what the plan has: the
    categories asked for or present, and a constrained side. With `wanted`, what the user
    asked for: exact counts as numbers, each constrained side as its range phrase, and an
    unconstrained side not at all, never as a made-up 0."""
    required, present = detail["required"], detail["present"]
    total = required.get("dishes", 0)
    phrases = side_phrases(detail_ranges(detail), total, zh)
    parts = []
    for category, (one, many, cn) in CATEGORY_WORDS.items():
        if wanted and category in SIDE_CATEGORIES:
            if category in phrases:
                parts.append(phrases[category])
            continue
        if category in SIDE_CATEGORIES:
            include = bool(present.get(category, 0)) or category in phrases
        else:
            include = bool(required.get(category, 0) or present.get(category, 0))
        if include:
            n = (required if wanted else present).get(category, 0)
            parts.append(f"{n} {cn}" if zh else f"{n} {one if n == 1 else many}")
    return ("，" if zh else ", ").join(parts)


def unmet_for_user(finding: dict, titles: dict[int, str], locale: str) -> str:
    code, detail = finding["code"], finding["detail"]
    title = titles.get(finding["recipe_id"], "")
    zh = is_zh(locale)
    if code == "H1":
        names = ("、" if zh else ", ").join(detail["missing"])
        count = len(detail["missing"])
        if zh:
            return f"{title}：缺少 {count} 种食材（{names}）"
        return f"{title}: {count} ingredient{'s' if count != 1 else ''} missing ({names})"
    if code == "H3":
        names = ("、" if zh else ", ").join(detail["missing"])
        if zh:
            return f"{title}：缺少厨具（{names}）"
        return f"{title}: missing cookware ({names})"
    if code == "H4":
        present = counts_text(detail, zh)
        wanted = counts_text(detail, zh, wanted=True)
        if zh:
            return f"方案有 {present}，要求是 {wanted}"
        return f"The plan has {present}; {wanted} were asked for"
    if code == "H7":
        lacking = ("、" if zh else ", ").join(
            f"“{titles[i]}”" if zh else f'"{titles[i]}"'
            for i in detail["recipes_without"]
            if i in titles
        )
        name = detail["name"]
        if detail["rule"] == "must":
            if zh:
                return f"方案里没有用到{name}的菜"
            return f"No dish in the plan uses {name}"
        if zh:
            return f"这些菜没有用到{name}：{lacking}"
        return f"These dishes do not use {name}: {lacking}"
    if code == "H6":
        minutes, limit = detail["time_min"], detail["limit"]
        if zh:
            return f"{title}：需要 {minutes} 分钟，超过 {limit} 分钟的限制"
        return f"{title}: takes {minutes} minutes, over the {limit}-minute limit"
    return f"{title}: {code}"


def no_plan_reason(code: str, locale: str, meal_avoid: Sequence[str] = ()) -> str:
    """Why no plan is shown (H2 or H5), without naming the dishes. `meal_avoid` is given when
    every H2 finding was this meal's one-meal avoidance (plan 3b-1b spec 5.4): the reason then
    names the meal's wish, never a diner's rule."""
    zh = is_zh(locale)
    if code == "H2" and meal_avoid:
        if zh:
            return f"这顿不吃{'、'.join(meal_avoid)}，菜谱库里没有足够不含它的菜"
        return f"No plan fits leaving out {', '.join(meal_avoid)} at this meal"
    if code == "H2":
        if zh:
            return "找到的方案都含有用餐者过敏或忌口的食材"
        return "Every plan found had a dish that breaks a diner's allergies or dietary restrictions"
    if zh:
        return "提议的菜并不都来自你的菜谱库"
    return "The proposed dishes were not all from your recipe library"


# A dish type in a sentence about all of them: (English plural, Chinese).
CATEGORY_KIND = {
    "meat_dishes": ("meat dishes", "荤菜"),
    "vegetable_dishes": ("vegetable dishes", "素菜"),
    "soups": ("soups", "汤"),
    "staples": ("staple foods", "主食"),
    "desserts": ("desserts", "甜品"),
    "snacks": ("snacks", "小吃"),
}


def count_phrase(n: int | None, category: str, zh: bool, minimum: bool = False) -> str:
    one, many, cn = CATEGORY_WORDS[category]
    if zh:
        return f"{'至少 ' if minimum else ''}{n} {cn}"
    return f"{'at least ' if minimum else ''}{n} {one if n == 1 else many}"


def reason_text(shortfall: dict, mode: str, locale: str) -> str:
    """One feasibility reason in plain words, in the request's locale. Fixed strings that
    state what the meal needs and what is available; never model output."""
    zh = is_zh(locale)
    reason, category = shortfall["reason"], shortfall["category"]
    needed, available = shortfall["needed"], shortfall["available"]
    name = shortfall.get("ingredient")
    fridge = mode == "fridge_only"
    minimum = bool(shortfall.get("minimum"))
    if reason == "empty_inventory":
        return "冰箱里没有可以用来做菜的食材" if zh else "There is nothing in the fridge to cook with"
    # A category names the dish type ("None of your vegetable dishes ..."); the combined
    # "dishes" line and no category keep the generic sentence.
    kind = CATEGORY_KIND.get(category or "")
    if reason == "no_cookware":
        if kind:
            return f"你的{kind[1]}都用不了现有的厨具" if zh else (
                f"None of your {kind[0]} can be made with the cookware you have"
            )
        if zh:
            return "你的菜谱里没有一道能用你现有的厨具做出来"
        return "None of your recipes can be made with the cookware you have"
    if reason == "time_limit":
        if kind:
            return f"你的{kind[1]}都不符合时间限制" if zh else (
                f"None of your {kind[0]} fits the time limit"
            )
        return "你的菜谱里没有一道符合时间限制" if zh else "None of your recipes fits the time limit"
    if reason == "not_enough_dishes":
        have = count_phrase(available, category, zh)
        want = count_phrase(needed, category, zh, minimum)
        if zh:
            return f"按冰箱现有的食材只能做 {have}，这餐需要 {want}"
        return f"By the current fridge you can make {have}; this meal needs {want}"
    if reason == "not_enough_in_library":
        have = count_phrase(available, category, zh)
        want = count_phrase(needed, category, zh, minimum)
        if zh:
            return f"你的菜谱库里你能吃的只有 {have}，这餐需要 {want}"
        return f"Your library has {have} you can eat; this meal needs {want}"
    # named_ingredient
    if category is None:
        if fridge and shortfall.get("absent_from_stock"):
            return f"冰箱里没有{name}" if zh else f"No {name} in the fridge"
        if fridge:
            if zh:
                return f"用现有的食材做不出用到{name}的菜"
            return f"No dish that can be made from the fridge uses {name}"
        if zh:
            return f"你的菜谱库里没有用到{name}的菜"
        return f"No dish in your library uses {name}"
    have = count_phrase(available, category, zh)
    want = count_phrase(needed, category, zh, minimum)
    if zh:
        verb = "只能做" if fridge else "只有"
        return f"用到{name}的菜{verb} {have}，这餐需要 {want}"
    if fridge:
        return f"Only {have} with {name} can be made; this meal needs {want}"
    return f"Your library has only {have} with {name}; this meal needs {want}"


# Every next action, in the order the card shows them.
NEXT_ACTION_ORDER = (
    "switch_to_shopping",
    "fewer_dishes",
    "drop_time_limit",
    "relax_split",
    "relax_named",
    "update_inventory",
    "register_cookware",
    "import_recipes",
)
SIDE_CATEGORIES = ("meat_dishes", "vegetable_dishes")
# Every reason and unmet rule that offers fewer_dishes: the design 5.3 table plus H2 (ruling
# after review); H5 alone gets no action.
_FEWER_DISHES_CODES = {
    "empty_inventory", "no_cookware", "not_enough_dishes", "time_limit", "not_enough_in_library",
    "named_ingredient", "H1", "H2", "H3", "H4", "H6", "H7",
}


def next_actions(
    spec: dict,
    reasons: list[str],
    unmet_codes: list[str],
    short_categories: list[str] | None = None,
) -> list[str]:
    """The next actions that fit the reasons and unmet rules (design 5.3): their union, in
    NEXT_ACTION_ORDER. `short_categories` are the feasibility categories that fell short;
    when given, relax_split needs a meat or vegetable category among them."""
    codes = set(reasons) | set(unmet_codes)
    fridge = spec["mode"] == "fridge_only"
    total = sum(spec.get(n, 0) for n in ("dishes", "soups", "staples", "desserts", "snacks"))
    chosen: set[str] = set()
    if fridge and codes & {"empty_inventory", "not_enough_dishes", "named_ingredient", "H1", "H2"}:
        chosen.add("switch_to_shopping")
    if total > 1 and codes & _FEWER_DISHES_CODES:
        chosen.add("fewer_dishes")
    if codes & {"time_limit", "H6"}:
        chosen.add("drop_time_limit")
    names_split = spec.get("meat_dishes") is not None or spec.get("vegetable_dishes") is not None
    if names_split:
        if short_categories is None:
            side_short = bool(codes & {"not_enough_in_library", "not_enough_dishes"})
        else:
            side_short = any(c in SIDE_CATEGORIES for c in short_categories)
        if side_short or "H4" in codes:
            chosen.add("relax_split")
    if codes & {"named_ingredient", "H7"}:
        chosen.add("relax_named")
    if codes & {"empty_inventory", "not_enough_dishes", "H1"} or (
        fridge and "named_ingredient" in codes
    ):
        chosen.add("update_inventory")
    if codes & {"no_cookware", "H3"}:
        chosen.add("register_cookware")
    if codes & {"not_enough_in_library", "H4", "named_ingredient", "H7"}:
        chosen.add("import_recipes")
    return [a for a in NEXT_ACTION_ORDER if a in chosen]
