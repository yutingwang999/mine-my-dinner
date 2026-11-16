"""User-created dictionary entries. A term can belong to only one entry.

Seed entries are reviewed by a person and are read-only.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import distinct, exists, func, select, update
from sqlalchemy.orm import Session

from minemydinner.constants import ALLERGENS, CATEGORIES, STOCK_MODES
from minemydinner.extraction.normalize import all_terms, find_term_owner
from minemydinner.models import Cookware, DietaryRule, Ingredient, Inventory, RecipeIngredient

CJK = re.compile(r"[㐀-鿿]")


class TermConflict(Exception):
    def __init__(self, term: str, owner_id: int) -> None:
        super().__init__(term)
        self.term = term
        self.owner_id = owner_id


class HierarchyError(Exception):
    pass


class ReadOnlyEntry(Exception):
    pass


class EntryNotFound(Exception):
    pass


class EntryInUse(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _clean(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def _clean_aliases(values: list[str]) -> list[str]:
    cleaned: dict[str, str] = {}
    for value in values:
        if value.strip():
            cleaned.setdefault(value.strip().lower(), value.strip())
    return list(cleaned.values())


def _unique_allergens(values: list[str] | None) -> list[str] | None:
    return None if values is None else list(dict.fromkeys(values))


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


class DictionaryPatch(BaseModel):
    """Only the fields sent are changed. An explicit null allergens means unknown."""

    name_zh: str | None = None
    name_en: str | None = None
    aliases_zh: list[str] | None = None
    aliases_en: list[str] | None = None


class IngredientPatch(DictionaryPatch):
    category: Literal[*CATEGORIES] | None = None
    stock_mode: Literal[*STOCK_MODES] | None = None
    allergens: list[Literal[*ALLERGENS]] | None = None
    parent_id: int | None = None


def _check_terms(
    session: Session, table: type, data: DictionaryWrite, own_id: int | None = None
) -> None:
    for term in data.terms():
        owner = find_term_owner(session, table, term)
        if owner is not None and owner.id != own_id:
            raise TermConflict(term, owner.id)


def _check_parent(session: Session, parent_id: int | None, entry_id: int | None = None) -> None:
    if parent_id is None:
        return
    parent = session.get(Ingredient, parent_id)
    # The hierarchy has one level, so a parent must itself be top-level.
    if parent is None or parent.parent_id is not None or parent_id == entry_id:
        raise HierarchyError("the parent must be another existing top-level entry")
    if entry_id is not None:
        has_children = session.scalar(select(exists().where(Ingredient.parent_id == entry_id)))
        if has_children:
            raise HierarchyError("an entry with children cannot have a parent")


def create_user_ingredient(session: Session, data: IngredientWrite) -> Ingredient:
    _check_terms(session, Ingredient, data)
    _check_parent(session, data.parent_id)
    entry = Ingredient(
        **data.model_dump(exclude={"allergens"}),
        allergens=_unique_allergens(data.allergens),
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


def _user_entry(session: Session, table: type, entry_id: int) -> Any:
    entry = session.get(table, entry_id)
    if entry is None:
        raise EntryNotFound()
    if entry.source != "user":
        raise ReadOnlyEntry()
    return entry


def _merged(entry: Any, patch: DictionaryPatch, model: type[DictionaryWrite]) -> DictionaryWrite:
    current = {field: getattr(entry, field) for field in model.model_fields}
    return model.model_validate(current | patch.model_dump(exclude_unset=True))


def update_user_ingredient(
    session: Session, entry_id: int, patch: IngredientPatch
) -> Ingredient:
    entry = _user_entry(session, Ingredient, entry_id)
    data = _merged(entry, patch, IngredientWrite)
    _check_terms(session, Ingredient, data, own_id=entry.id)
    if "parent_id" in patch.model_fields_set:
        _check_parent(session, data.parent_id, entry.id)
    if data.stock_mode != entry.stock_mode and session.get(Inventory, entry.id) is not None:
        # The inventory row's fields follow stock_mode, so they would no longer fit.
        raise EntryInUse("in_inventory")
    for field, value in data.model_dump(exclude={"allergens"}).items():
        setattr(entry, field, value)
    entry.allergens = _unique_allergens(data.allergens)
    session.flush()
    return entry


def delete_user_ingredient(session: Session, entry_id: int) -> None:
    """Recipe lines using the entry return to unmatched; inventory rows go with it."""
    entry = _user_entry(session, Ingredient, entry_id)
    if session.scalar(select(exists().where(Ingredient.parent_id == entry.id))):
        raise EntryInUse("has_children")
    if session.scalar(select(exists().where(DietaryRule.ingredient_id == entry.id))):
        raise EntryInUse("used_by_rule")
    session.delete(entry)
    session.flush()


def update_user_cookware(session: Session, entry_id: int, patch: DictionaryPatch) -> Cookware:
    entry = _user_entry(session, Cookware, entry_id)
    data = _merged(entry, patch, CookwareWrite)
    _check_terms(session, Cookware, data, own_id=entry.id)
    for field, value in data.model_dump().items():
        setattr(entry, field, value)
    session.flush()
    return entry


def delete_user_cookware(session: Session, entry_id: int) -> None:
    session.delete(_user_entry(session, Cookware, entry_id))
    session.flush()


def link_unmatched_lines(session: Session, ingredient: Ingredient) -> int:
    """Link unmatched recipe lines whose name is one of the entry's names, aliases or
    personal aliases.

    Names compare as dictionary matching does: ignoring case and outer spaces.
    """
    terms = {name.strip().lower() for name in all_terms(ingredient)}
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


def set_personal_aliases(
    session: Session, entry_id: int, aliases_zh: list[str], aliases_en: list[str]
) -> tuple[Ingredient, int]:
    """Replace the user's own aliases on a built-in entry and link matching lines.

    Returns the entry and how many recipe lines were linked.
    """
    entry = session.get(Ingredient, entry_id)
    if entry is None:
        raise EntryNotFound()
    if entry.source == "user":
        raise ReadOnlyEntry()
    zh, en = _clean_aliases(aliases_zh), _clean_aliases(aliases_en)
    for term in [*zh, *en]:
        owner = find_term_owner(session, Ingredient, term)
        if owner is not None and owner.id != entry.id:
            raise TermConflict(term, owner.id)
    entry.user_aliases_zh = zh
    entry.user_aliases_en = en
    session.flush()
    return entry, link_unmatched_lines(session, entry)


def remember_alias(
    session: Session, entry: Ingredient, name: str
) -> Literal["added", "exists", "conflict"]:
    """Add a name the user linked by hand to the entry as an alias.

    A built-in entry keeps it as a personal alias; the user's own entry keeps it as an
    alias. Nothing is written when another entry already owns the name.
    """
    name = name.strip()
    owner = find_term_owner(session, Ingredient, name)
    if owner is not None:
        return "exists" if owner.id == entry.id else "conflict"
    personal = entry.source != "user"
    side = "zh" if CJK.search(name) else "en"
    field = f"{'user_' if personal else ''}aliases_{side}"
    setattr(entry, field, [*getattr(entry, field), name])
    session.flush()
    link_unmatched_lines(session, entry)
    return "added"


@dataclass(frozen=True)
class UnresolvedEntry:
    id: int
    name: str
    dish_count: int


@dataclass(frozen=True)
class UnresolvedName:
    name: str
    dish_count: int


def unresolved_allergens(session: Session) -> tuple[list[UnresolvedEntry], list[UnresolvedName]]:
    """Entries with unknown allergens, and names of unmatched lines, with dish counts."""
    dish_count = (
        select(func.count(distinct(RecipeIngredient.recipe_id)))
        .where(RecipeIngredient.ingredient_id == Ingredient.id)
        .scalar_subquery()
    )
    entry_rows = session.execute(
        select(Ingredient, dish_count.label("dish_count"))
        .where(Ingredient.allergens.is_(None))
        .order_by(dish_count.desc(), Ingredient.id)
    ).all()
    entries = [
        UnresolvedEntry(entry.id, entry.name_zh or entry.name_en or "", count)
        for entry, count in entry_rows
    ]
    key = func.lower(func.trim(RecipeIngredient.name))
    count = func.count(distinct(RecipeIngredient.recipe_id))
    name_rows = session.execute(
        select(func.min(func.trim(RecipeIngredient.name)), count)
        .where(RecipeIngredient.ingredient_id.is_(None))
        .group_by(key)
        .order_by(count.desc(), func.min(RecipeIngredient.name))
    ).all()
    return entries, [UnresolvedName(name, dishes) for name, dishes in name_rows]


def resolve_allergens(
    session: Session, name: str | None, ingredient_id: int | None, allergens: list[str]
) -> tuple[Ingredient, int]:
    """Record the user's confirmed allergens, creating an entry for an unmatched name.

    Returns the entry and how many recipe lines were linked to it.
    """
    if ingredient_id is not None:
        entry = _user_entry(session, Ingredient, ingredient_id)
    else:
        name = (name or "").strip()
        entry = find_term_owner(session, Ingredient, name)
        if entry is None:
            # Category and stock mode are guesses; the user can change them later.
            field = "name_zh" if CJK.search(name) else "name_en"
            entry = Ingredient(
                **{field: name}, category="other", stock_mode="level", source="user"
            )
            session.add(entry)
    if entry.source == "user":
        entry.allergens = _unique_allergens(allergens)
    session.flush()
    return entry, link_unmatched_lines(session, entry)
