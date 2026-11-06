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


def _enforce_line(line: ExtractedIngredient) -> ExtractedIngredient:
    if line.amount_type in ("vague", "missing"):
        return line.model_copy(update={"amount": None, "unit": None})
    if states_an_amount(line.raw):
        return line
    name_only = line.raw.strip().casefold() == line.name.strip().casefold()
    return line.model_copy(
        update={"amount": None, "unit": None, "amount_type": "missing" if name_only else "vague"}
    )


def enforce_amount_rules(recipe: ExtractedRecipe) -> ExtractedRecipe:
    """Remove amounts the source does not state, so a model can never invent one."""
    update: dict = {"ingredients": [_enforce_line(line) for line in recipe.ingredients]}
    if recipe.time_min is None:
        update["time_source"] = None
    return recipe.model_copy(update=update)
