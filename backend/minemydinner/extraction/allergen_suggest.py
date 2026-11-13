"""Suggest allergens for a new ingredient. A suggestion has no effect until the user confirms."""

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from minemydinner.constants import ALLERGENS
from minemydinner.llm.client import AllModelsFailed, LLMClient, MonthlyCapExceeded

PROMPT = (
    "List which of these allergens the ingredient may contain: "
    + ", ".join(ALLERGENS)
    + ". Use only these exact values. Include an allergen when a common version of the "
    "ingredient contains it. Return an empty list when none apply."
)


class AllergenSuggestion(BaseModel):
    # Plain strings, so an unlisted value is discarded here instead of failing the call.
    allergens: list[str]


def suggest_allergens(client: LLMClient, name: str) -> list[str] | None:
    """Listed allergens the model suggests, or None when no model is available."""
    messages = [SystemMessage(PROMPT), HumanMessage(f"Ingredient: {name}")]
    try:
        result = client.invoke_structured("allergen_suggestions", messages, AllergenSuggestion)
    except (AllModelsFailed, MonthlyCapExceeded):
        return None
    suggested = {value.strip().lower() for value in result.parsed.allergens}
    return [allergen for allergen in ALLERGENS if allergen in suggested]
