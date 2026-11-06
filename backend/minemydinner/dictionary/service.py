"""User-created dictionary entries. A term can belong to only one entry."""

from typing import Literal, Self

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from minemydinner.constants import ALLERGENS, CATEGORIES, STOCK_MODES
from minemydinner.extraction.normalize import find_term_owner
from minemydinner.models import Cookware, Ingredient, RecipeIngredient


class TermConflict(Exception):
    def __init__(self, term: str, owner_id: int) -> None:
        super().__init__(term)
        self.term = term
        self.owner_id = owner_id


class HierarchyError(Exception):
    pass


def _clean(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def _clean_aliases(values: list[str]) -> list[str]:
    cleaned: dict[str, str] = {}
    for value in values:
        if value.strip():
            cleaned.setdefault(value.strip().lower(), value.strip())
    return list(cleaned.values())


class DictionaryWrite(BaseModel):
    name_zh: str | None = None
    name_en: str | None = None
    aliases_zh: list[str] = []
    aliases_en: list[str] = []

    @field_validator("name_zh", "name_en")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return _clean(value)

    @field_validator("aliases_zh", "aliases_en")
    @classmethod
    def clean_aliases(cls, values: list[str]) -> list[str]:
        return _clean_aliases(values)

    @model_validator(mode="after")
    def has_a_name(self) -> Self:
        if self.name_zh is None and self.name_en is None:
            raise ValueError("an entry needs a Chinese or English name")
        return self

    def terms(self) -> list[str]:
        names = [name for name in (self.name_zh, self.name_en) if name]
        return [*names, *self.aliases_zh, *self.aliases_en]


class IngredientWrite(DictionaryWrite):
    category: Literal[*CATEGORIES]
    stock_mode: Literal[*STOCK_MODES]
    # None means unknown. An empty list means the user confirmed there are none.
    allergens: list[Literal[*ALLERGENS]] | None = None
    parent_id: int | None = None


class CookwareWrite(DictionaryWrite):
    pass


def _check_terms(session: Session, table: type, data: DictionaryWrite) -> None:
    for term in data.terms():
        owner = find_term_owner(session, table, term)
        if owner is not None:
            raise TermConflict(term, owner.id)


def create_user_ingredient(session: Session, data: IngredientWrite) -> Ingredient:
    _check_terms(session, Ingredient, data)
    if data.parent_id is not None:
        parent = session.get(Ingredient, data.parent_id)
        # The hierarchy has one level, so a parent must itself be top-level.
        if parent is None or parent.parent_id is not None:
            raise HierarchyError("the parent must be an existing top-level entry")
    entry = Ingredient(
        **data.model_dump(exclude={"allergens"}),
        allergens=None if data.allergens is None else list(dict.fromkeys(data.allergens)),
        source="user",
    )
    session.add(entry)
    session.flush()
    return entry


def create_user_cookware(session: Session, data: CookwareWrite) -> Cookware:
    _check_terms(session, Cookware, data)
    entry = Cookware(**data.model_dump(), source="user")
    session.add(entry)
    session.flush()
    return entry


def link_unmatched_lines(session: Session, ingredient: Ingredient) -> int:
    """Link unmatched recipe lines whose name is one of the entry's names or aliases.

    Names compare as dictionary matching does: ignoring case and outer spaces.
    """
    names = [ingredient.name_zh, ingredient.name_en, *ingredient.aliases_zh, *ingredient.aliases_en]
    terms = {name.strip().lower() for name in names if name}
    result = session.execute(
        update(RecipeIngredient)
        .where(
            RecipeIngredient.ingredient_id.is_(None),
            func.lower(func.trim(RecipeIngredient.name)).in_(terms),
        )
        .values(ingredient_id=ingredient.id)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
