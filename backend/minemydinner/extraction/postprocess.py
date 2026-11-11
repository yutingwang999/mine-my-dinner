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
CHINESE_WORDS_FOR = {
    0.5: ["半"],
    1: ["一"],
    2: ["两", "二"],
    3: ["三"],
    4: ["四"],
    5: ["五"],
    6: ["六"],
    7: ["七"],
    8: ["八"],
    9: ["九"],
    10: ["十"],
}


def _amount_words(amount: float) -> list[str]:
    """The ways the source could spell this amount, such as 2, 两, or 二."""
    number = str(int(amount)) if amount == int(amount) else str(amount)
    return [number, *CHINESE_WORDS_FOR.get(amount, [])]


def _source_states_amount(name: str, amount: float | None, source: str) -> bool:
    """True when the source states this very amount next to the name, as a method step
    does. A neighbouring line's number or a step number must not corroborate it."""
    if amount is None:
        return False
    words = _amount_words(amount)
    for match in re.finditer(re.escape(name), source):
        before = source[max(0, match.start() - AMOUNT_WINDOW) : match.start()]
        after = source[match.end() : match.end() + AMOUNT_WINDOW]
        if any(word in before or word in after for word in words):
            return True
    return False


def _enforce_line(line: ExtractedIngredient, source: str | None) -> ExtractedIngredient:
    if line.amount_type in ("vague", "missing"):
        return line.model_copy(update={"amount": None, "unit": None})
    if states_an_amount(line.raw):
        return line
    # An ingredient-list line without an amount; the method may still state one.
    if source and _source_states_amount(line.name, line.amount, source):
        return line
    name_only = line.raw.strip().casefold() == line.name.strip().casefold()
    return line.model_copy(
        update={"amount": None, "unit": None, "amount_type": "missing" if name_only else "vague"}
    )


def _sound_groups(lines: list[ExtractedIngredient]) -> list[ExtractedIngredient]:
    """Make the model's groups reviewable: a lone group member becomes an ordinary line,
    and a group's members are optional together (optional only when every member is)."""
    groups = [line.choice_group for line in lines if line.choice_group is not None]
    optional_groups = {
        group
        for group in groups
        if all(line.optional for line in lines if line.choice_group == group)
    }
    result = []
    for line in lines:
        if line.choice_group is None:
            result.append(line)
        elif groups.count(line.choice_group) < 2:
            result.append(line.model_copy(update={"choice_group": None}))
        else:
            result.append(
                line.model_copy(update={"optional": line.choice_group in optional_groups})
            )
    return result


def enforce_amount_rules(
    recipe: ExtractedRecipe, source_text: str | None = None
) -> ExtractedRecipe:
    """Remove amounts the source does not state, so a model can never invent one."""
    lines = _sound_groups([_enforce_line(line, source_text) for line in recipe.ingredients])
    update: dict = {"ingredients": lines}
    if recipe.time_min is None:
        update["time_source"] = None
    return recipe.model_copy(update=update)
