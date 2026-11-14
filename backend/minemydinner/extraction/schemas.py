from typing import Literal

from pydantic import BaseModel, Field

from minemydinner.constants import AMOUNT_TYPES, DISH_TYPES, LANGUAGES, ROLES, UNITS


class ExtractedIngredient(BaseModel):
    raw: str
    name: str
    amount: float | None
    unit: Literal[*UNITS] | None
    amount_type: Literal[*AMOUNT_TYPES]
    role: Literal[*ROLES]
    optional: bool = False
    # Lines sharing a value are alternatives ("pick one of these"); null is an ordinary line.
    choice_group: int | None = None


class ExtractedRecipe(BaseModel):
    lang: Literal[*LANGUAGES]
    title: str
    dish_type: Literal[*DISH_TYPES]
    time_min: int | None
    # `user` is set only when the user edits the time, never by the model.
    time_source: Literal["stated", "estimated"] | None
    servings: int | None
    tags: list[str]
    steps_summary: str | None
    ingredients: list[ExtractedIngredient]
    cookware: list[str]
    # Consumables such as plastic wrap or foil: a reminder, never checked when planning.
    supplies: list[str] = []
    # How many distinct recipes the input holds; only the first is extracted. Never stored.
    recipe_count: int = Field(default=1, ge=1)
