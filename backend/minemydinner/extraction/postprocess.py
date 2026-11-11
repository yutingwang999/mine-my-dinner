import re

from minemydinner.extraction.schemas import ExtractedIngredient, ExtractedRecipe

CHINESE_NUMERALS = "一二两三四五六七八九十半"
# Phrases that contain a numeral but state no amount, such as "一点盐" (a little salt).
CHINESE_VAGUE_PHRASES = ("一点点", "一点", "一些", "一下")
ENGLISH_NUMBER_WORDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|dozen|half|quarter|third)\b",
    re.IGNORECASE,
)


def states_an_amount(raw: str) -> bool:
    text = raw
    for phrase in CHINESE_VAGUE_PHRASES:
        text = text.replace(phrase, "")
    # isnumeric covers full-width digits and fractions such as "½".
    if any(char.isnumeric() or char in CHINESE_NUMERALS for char in text):
        return True
    return ENGLISH_NUMBER_WORDS.search(text) is not None


# How far around the name an amount may sit, as in "生抽4勺" or "2 tbsp butter".
AMOUNT_WINDOW = 8


def _source_states_amount(name: str, source: str) -> bool:
    """True when the source states a number next to the name, as a method step does."""
    for match in re.finditer(re.escape(name), source):
        before = source[max(0, match.start() - AMOUNT_WINDOW) : match.start()]
        after = source[match.end() : match.end() + AMOUNT_WINDOW]
        if states_an_amount(before) or states_an_amount(after):
            return True
    return False


def _enforce_line(line: ExtractedIngredient, source: str | None) -> ExtractedIngredient:
    if line.amount_type in ("vague", "missing"):
        return line.model_copy(update={"amount": None, "unit": None})
    if states_an_amount(line.raw):
        return line
    # An ingredient-list line without an amount; the method may still state one.
    if source and _source_states_amount(line.name, source):
        return line
    name_only = line.raw.strip().casefold() == line.name.strip().casefold()
    return line.model_copy(
        update={"amount": None, "unit": None, "amount_type": "missing" if name_only else "vague"}
    )


def _drop_singleton_groups(lines: list[ExtractedIngredient]) -> list[ExtractedIngredient]:
    """A choice group needs two lines; a model's lone group must not block the review."""
    groups = [line.choice_group for line in lines if line.choice_group is not None]
    return [
        line.model_copy(update={"choice_group": None})
        if line.choice_group is not None and groups.count(line.choice_group) < 2
        else line
        for line in lines
    ]


def enforce_amount_rules(
    recipe: ExtractedRecipe, source_text: str | None = None
) -> ExtractedRecipe:
    """Remove amounts the source does not state, so a model can never invent one."""
    lines = _drop_singleton_groups(
        [_enforce_line(line, source_text) for line in recipe.ingredients]
    )
    update: dict = {"ingredients": lines}
    if recipe.time_min is None:
        update["time_source"] = None
    return recipe.model_copy(update=update)
