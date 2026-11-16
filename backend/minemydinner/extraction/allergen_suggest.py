"""Suggest allergens, category, and stock mode for a new ingredient.

A suggestion has no effect until the user confirms it.
"""

from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from minemydinner.constants import ALLERGENS, CATEGORIES, STOCK_MODES
from minemydinner.llm.client import AllModelsFailed, LLMClient, MonthlyCapExceeded

PROMPT_VERSION = "allergen_suggest_v2"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(version: str = PROMPT_VERSION) -> str:
    # The files end with one newline, which is not part of the prompt.
    text = (PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")
    return text.removesuffix("\n")


class EntrySuggestion(BaseModel):
    # Plain strings, so an unlisted value is discarded here instead of failing the call.
    allergens: list[str]
    category: str | None = None
    stock_mode: str | None = None


@dataclass(frozen=True)
class Suggestion:
    allergens: list[str]
    category: str | None
    stock_mode: str | None


def _listed(value: str | None, allowed: tuple[str, ...]) -> str | None:
    cleaned = (value or "").strip().lower()
    return cleaned if cleaned in allowed else None


def suggest_entry(client: LLMClient, name: str) -> Suggestion | None:
    """The model's suggestion, or None when no model is available."""
    messages = [SystemMessage(load_prompt()), HumanMessage(f"Ingredient: {name}")]
    try:
        result = client.invoke_structured("allergen_suggestions", messages, EntrySuggestion)
    except (AllModelsFailed, MonthlyCapExceeded):
        return None
    parsed = result.parsed
    suggested = {value.strip().lower() for value in parsed.allergens}
    return Suggestion(
        allergens=[allergen for allergen in ALLERGENS if allergen in suggested],
        category=_listed(parsed.category, CATEGORIES),
        stock_mode=_listed(parsed.stock_mode, STOCK_MODES),
    )
