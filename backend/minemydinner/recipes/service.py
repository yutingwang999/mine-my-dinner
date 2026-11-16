from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from minemydinner.constants import (
    DISH_TYPES,
    LANGUAGES,
    SOURCE_TYPES,
    TIME_SOURCES,
    UNIT_AMOUNT_TYPES,
)
from minemydinner.dictionary.service import remember_alias
from minemydinner.imports.service import DraftIngredient, Provenance
from minemydinner.llm.client import LLMClient
from minemydinner.models import (
    Cookware,
    ExtractionLog,
    Ingredient,
    Recipe,
    RecipeCookware,
    RecipeIngredient,
)
from minemydinner.recipes.embedding import try_embed_recipe
from minemydinner.recipes.images import remove_unused_files


KEPT_TEXT_SOURCES = ("text", "batch")


class InvalidRecipe(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RecipeLineWrite(DraftIngredient):
    # Asks to keep the line's name as an alias of the linked entry. Not stored.
    remember_link: bool = False

    @field_validator("raw", "name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def no_number_without_amount(self) -> Self:
        if self.amount_type in ("vague", "missing") and (self.amount is not None or self.unit):
            raise ValueError(f"a {self.amount_type} amount has no number or unit")
        return self

    @model_validator(mode="after")
    def amount_type_matches_unit(self) -> Self:
        # The model's raw output is not checked; only what the user saves.
        if self.amount is None:
            return self
        if self.amount <= 0:
            raise ValueError("an amount must be more than 0")
        if self.amount_type != UNIT_AMOUNT_TYPES[self.unit]:
            raise ValueError(f"unit {self.unit} needs amount type {UNIT_AMOUNT_TYPES[self.unit]}")
        return self


class RecipeWrite(BaseModel):
    """The recipe the user reviewed. The client sets time_source to user on a time edit."""

    lang: Literal[*LANGUAGES]
    title: str
    dish_type: Literal[*DISH_TYPES]
    time_min: int | None = Field(default=None, gt=0)
    time_source: Literal[*TIME_SOURCES] | None = None
    servings: int | None = Field(default=None, gt=0)
    tags: list[str] = []
    supplies: list[str] = []
    steps_summary: str | None = None
    ingredients: list[RecipeLineWrite] = Field(min_length=1)
    cookware_ids: list[int] = []
    source_type: Literal[*SOURCE_TYPES]
    source_url: str | None = None
    provenance: Provenance | None = None

    @field_validator("supplies")
    @classmethod
    def clean_supplies(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def time_source_needs_time(self) -> Self:
        if self.time_source is not None and self.time_min is None:
            raise ValueError("time_source needs time_min")
        return self

    @model_validator(mode="after")
    def choice_groups_are_sound(self) -> Self:
        grouped: dict[int, list[bool]] = {}
        for line in self.ingredients:
            if line.choice_group is not None:
                grouped.setdefault(line.choice_group, []).append(line.optional)
        for members in grouped.values():
            if len(members) < 2:
                raise ValueError("a choice group needs at least two lines")
            if len(set(members)) > 1:
                raise ValueError("lines of one choice group are optional together")
        return self


def _check_references(session: Session, data: RecipeWrite) -> None:
    ingredient_ids = {line.ingredient_id for line in data.ingredients} - {None}
    if ingredient_ids:
        found = session.scalars(select(Ingredient.id).where(Ingredient.id.in_(ingredient_ids)))
        if set(found) != ingredient_ids:
            raise InvalidRecipe("unknown_ingredient")
    cookware_ids = set(data.cookware_ids)
    if cookware_ids:
        found = session.scalars(select(Cookware.id).where(Cookware.id.in_(cookware_ids)))
        if set(found) != cookware_ids:
            raise InvalidRecipe("unknown_cookware")


def _apply(recipe: Recipe, data: RecipeWrite) -> None:
    recipe.lang = data.lang
    recipe.title = data.title
    recipe.dish_type = data.dish_type
    recipe.time_min = data.time_min
    recipe.time_source = data.time_source
    recipe.servings = data.servings
    recipe.tags = data.tags
    recipe.supplies = data.supplies
    recipe.steps_summary = data.steps_summary
    recipe.source_type = data.source_type
    recipe.source_url = data.source_url
    recipe.ingredients = [
        RecipeIngredient(**line.model_dump(exclude={"remember_link"}))
        for line in data.ingredients
    ]
    recipe.cookware = [
        RecipeCookware(cookware_id=cookware_id) for cookware_id in dict.fromkeys(data.cookware_ids)
    ]


def _write_log(session: Session, data: RecipeWrite, max_log_rows: int) -> ExtractionLog | None:
    """Keep the model output next to the user's correction, for evaluation."""
    if data.provenance is None:
        return None
    if session.scalar(select(func.count()).select_from(ExtractionLog)) >= max_log_rows:
        return None
    log = ExtractionLog(
        input_text=data.provenance.input_text,
        model_output=data.provenance.model_output,
        user_corrected=data.model_dump(
            mode="json",
            exclude={"provenance": True, "ingredients": {"__all__": {"remember_link"}}},
        ),
        model_name=data.provenance.model_name,
        prompt_version=data.provenance.prompt_version,
    )
    session.add(log)
    return log


def _remember_links(session: Session, data: RecipeWrite) -> list[str]:
    """Remember the linked lines' names as aliases. Returns the names another entry owns."""
    conflicts: list[str] = []
    for line in data.ingredients:
        if not line.remember_link or line.ingredient_id is None:
            continue
        entry = session.get(Ingredient, line.ingredient_id)
        if remember_alias(session, entry, line.name) == "conflict" and line.name not in conflicts:
            conflicts.append(line.name)
    return conflicts


def _source_text(data: RecipeWrite) -> str | None:
    """Pasted text and batch parts are kept as the full method; a web page keeps only its
    link, and screenshots are kept as images. See design 4.2."""
    if data.provenance is None or data.source_type not in KEPT_TEXT_SOURCES:
        return None
    return data.provenance.input_text


def create_recipe(
    session: Session, client: LLMClient, data: RecipeWrite, max_log_rows: int
) -> tuple[Recipe, ExtractionLog | None, list[str]]:
    """Commit the recipe before embedding it, so an embedding failure cannot lose it.

    Returns the extraction log too, so screenshots uploaded next can be added to it, and
    the names not remembered as aliases because another entry owns them.
    """
    _check_references(session, data)
    recipe = Recipe(source_text=_source_text(data))
    _apply(recipe, data)
    session.add(recipe)
    log = _write_log(session, data, max_log_rows)
    conflicts = _remember_links(session, data)
    session.commit()
    try_embed_recipe(session, client, recipe)
    return recipe, log, conflicts


def update_recipe(
    session: Session, client: LLMClient, recipe_id: int, data: RecipeWrite
) -> tuple[Recipe, list[str]]:
    recipe = session.get(Recipe, recipe_id)
    if recipe is None:
        raise InvalidRecipe("not_found")
    _check_references(session, data)
    _apply(recipe, data)
    recipe.embedding = None
    conflicts = _remember_links(session, data)
    session.commit()
    try_embed_recipe(session, client, recipe)
    return recipe, conflicts


def delete_recipe(session: Session, recipe_id: int, image_dir: Path) -> None:
    """Delete the recipe with its source text and its screenshots no one else uses."""
    recipe = session.get(Recipe, recipe_id)
    if recipe is None:
        raise InvalidRecipe("not_found")
    image_names = [image.file_name for image in recipe.images]
    session.delete(recipe)
    session.commit()
    remove_unused_files(session, image_dir, image_names)
