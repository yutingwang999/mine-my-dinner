from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.orm import Session

from minemydinner.api.deps import ApiError, contains_pattern, get_llm_client
from minemydinner.constants import ALLERGENS, CATEGORIES, DICTIONARY_SOURCES
from minemydinner.db import get_session
from minemydinner.dictionary.service import (
    CookwareWrite,
    DictionaryPatch,
    EntryInUse,
    EntryNotFound,
    HierarchyError,
    IngredientPatch,
    IngredientWrite,
    ReadOnlyEntry,
    TermConflict,
    create_user_cookware,
    create_user_ingredient,
    delete_user_cookware,
    delete_user_ingredient,
    link_name,
    link_unmatched_lines,
    resolve_allergens,
    set_personal_aliases,
    unresolved_allergens,
    update_user_cookware,
    update_user_ingredient,
)
from minemydinner.extraction.allergen_suggest import suggest_allergens
from minemydinner.extraction.normalize import match_ingredient, names_equal
from minemydinner.llm.client import LLMClient
from minemydinner.models import (
    Cookware,
    CookwareOwned,
    Ingredient,
    RecipeCookware,
    RecipeIngredient,
)

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]
ClientDep = Annotated[LLMClient, Depends(get_llm_client)]
SEARCH_LIMIT = 20
# Listing one source is for the dictionary page, which shows every entry.
SOURCE_LIMIT = 500
# Most names a draft can ask to match again in one request.
MATCH_LIMIT = 200


class IngredientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name_zh: str | None
    name_en: str | None
    aliases_zh: list[str]
    aliases_en: list[str]
    user_aliases_zh: list[str]
    user_aliases_en: list[str]
    category: str
    stock_mode: str
    allergens: list[str] | None
    parent_id: int | None
    source: str
    # How many recipes use the entry, so a delete can say what it affects.
    dish_count: int = 0


class LinkedIngredient(IngredientOut):
    linked_lines: int


class CookwareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name_zh: str | None
    name_en: str | None
    aliases_zh: list[str]
    aliases_en: list[str]
    source: str
    dish_count: int = 0


class CookwareIn(CookwareWrite):
    owned: bool


class PersonalAliasesIn(BaseModel):
    aliases_zh: list[str] = []
    aliases_en: list[str] = []


class SuggestionIn(BaseModel):
    name: str


class SuggestionOut(BaseModel):
    suggested: list[str] | None


class UnresolvedEntryOut(BaseModel):
    id: int
    name: str
    dish_count: int


class UnresolvedNameOut(BaseModel):
    name: str
    dish_count: int


class UnresolvedOut(BaseModel):
    entries: list[UnresolvedEntryOut]
    unmatched: list[UnresolvedNameOut]


class ResolveIn(BaseModel):
    """`allergens` has no default: the user must confirm a list or an empty list."""

    name: str | None = None
    ingredient_id: int | None = None
    allergens: list[Literal[*ALLERGENS]]

    @model_validator(mode="after")
    def one_target(self) -> Self:
        has_name = self.name is not None and bool(self.name.strip())
        if has_name == (self.ingredient_id is not None):
            raise ValueError("give either a name or an ingredient_id")
        return self


class MatchIn(BaseModel):
    names: list[str] = Field(max_length=MATCH_LIMIT)


class MatchOut(BaseModel):
    name: str
    entry: IngredientOut


class MatchesOut(BaseModel):
    matches: list[MatchOut]


class LinkIn(BaseModel):
    name: str
    ingredient_id: int
    remember: bool

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("the name must not be blank")
        return value


class LinkedByName(LinkedIngredient):
    remembered: bool


Source = Literal[*DICTIONARY_SOURCES]
# Browsing the whole dictionary asks for up to this many rows.
Limit = Annotated[int | None, Query(ge=1, le=SOURCE_LIMIT)]


def _search(
    session: Session,
    table: type,
    q: str | None,
    source: str | None,
    limit: int | None,
    category: str | None = None,
) -> list:
    query = select(table)
    if source is not None:
        query = query.where(table.source == source)
    if category is not None:
        query = query.where(table.category == category)
    if limit is None:
        limit = SOURCE_LIMIT if source is not None else SEARCH_LIMIT
    query = query.limit(limit)
    if q and q.strip():
        pattern = contains_pattern(q)
        alias_columns = [table.aliases_zh, table.aliases_en]
        if table is Ingredient:
            alias_columns += [table.user_aliases_zh, table.user_aliases_en]
        query = query.where(
            or_(
                table.name_zh.ilike(pattern),
                table.name_en.ilike(pattern),
                *(func.array_to_string(column, "\n").ilike(pattern) for column in alias_columns),
            )
        )
        # The entry the text names exactly comes first, so the limit never hides it.
        query = query.order_by(case((names_equal(table, q.strip().lower()), 0), else_=1))
    return list(session.scalars(query.order_by(table.id)))


def _dish_counts(session: Session, entry_column, recipe_column, ids: list[int]) -> dict[int, int]:
    """How many distinct recipes use each entry."""
    if not ids:
        return {}
    query = (
        select(entry_column, func.count(distinct(recipe_column)))
        .where(entry_column.in_(ids))
        .group_by(entry_column)
    )
    return dict(session.execute(query).all())


def _ingredient_outs(session: Session, entries: list[Ingredient]) -> list[IngredientOut]:
    counts = _dish_counts(
        session,
        RecipeIngredient.ingredient_id,
        RecipeIngredient.recipe_id,
        [entry.id for entry in entries],
    )
    return [
        IngredientOut.model_validate(entry).model_copy(
            update={"dish_count": counts.get(entry.id, 0)}
        )
        for entry in entries
    ]


def _cookware_outs(session: Session, entries: list[Cookware]) -> list[CookwareOut]:
    counts = _dish_counts(
        session,
        RecipeCookware.cookware_id,
        RecipeCookware.recipe_id,
        [entry.id for entry in entries],
    )
    return [
        CookwareOut.model_validate(entry).model_copy(
            update={"dish_count": counts.get(entry.id, 0)}
        )
        for entry in entries
    ]


@contextmanager
def _dictionary_errors() -> Iterator[None]:
    """Translate dictionary service errors into API errors."""
    try:
        yield
    except TermConflict as error:
        extra = {"term": error.term, "owner_id": error.owner_id}
        raise ApiError(409, "term_conflict", extra=extra) from error
    except HierarchyError as error:
        raise ApiError(422, "invalid_parent") from error
    except ReadOnlyEntry as error:
        raise ApiError(403, "seed_read_only") from error
    except EntryNotFound as error:
        raise ApiError(404, "not_found") from error
    except EntryInUse as error:
        raise ApiError(409, error.reason) from error


def _linked(session: Session, entry: Ingredient, linked: int) -> LinkedIngredient:
    [out] = _ingredient_outs(session, [entry])
    return LinkedIngredient(**out.model_dump(), linked_lines=linked)


def _committed[T](session: Session, action: Callable[[], T]) -> T:
    with _dictionary_errors():
        result = action()
    session.commit()
    return result


@router.get("/ingredients")
def search_ingredients(
    session: SessionDep,
    q: str | None = None,
    source: Source | None = None,
    category: Literal[*CATEGORIES] | None = None,
    limit: Limit = None,
) -> list[IngredientOut]:
    rows = _search(session, Ingredient, q, source, limit, category)
    return _ingredient_outs(session, rows)


@router.post("/ingredients/match")
def match_names(body: MatchIn, session: SessionDep) -> MatchesOut:
    """Match names against the dictionary again; names with no entry are left out."""
    found = [(name, match_ingredient(session, name)) for name in body.names]
    pairs = [(name, entry) for name, entry in found if entry is not None]
    outs = {out.id: out for out in _ingredient_outs(session, [e for _, e in pairs])}
    return MatchesOut(matches=[MatchOut(name=name, entry=outs[e.id]) for name, e in pairs])


@router.post("/ingredients", status_code=201)
def create_ingredient(data: IngredientWrite, session: SessionDep) -> LinkedIngredient:
    def create() -> LinkedIngredient:
        entry = create_user_ingredient(session, data)
        return _linked(session, entry, link_unmatched_lines(session, entry))

    return _committed(session, create)


@router.patch("/ingredients/{entry_id}")
def patch_ingredient(
    entry_id: int, patch: IngredientPatch, session: SessionDep
) -> LinkedIngredient:
    def change() -> LinkedIngredient:
        entry = update_user_ingredient(session, entry_id, patch)
        # A new name or alias may now match lines that were unmatched.
        return _linked(session, entry, link_unmatched_lines(session, entry))

    return _committed(session, change)


@router.put("/ingredients/{entry_id}/personal-aliases")
def put_personal_aliases(
    entry_id: int, body: PersonalAliasesIn, session: SessionDep
) -> LinkedIngredient:
    def change() -> LinkedIngredient:
        try:
            entry, linked = set_personal_aliases(
                session, entry_id, body.aliases_zh, body.aliases_en
            )
        except ReadOnlyEntry as error:
            # User-created entries edit their own aliases instead.
            raise ApiError(422, "not_seed") from error
        return _linked(session, entry, linked)

    return _committed(session, change)


@router.delete("/ingredients/{entry_id}", status_code=204)
def delete_ingredient(entry_id: int, session: SessionDep) -> Response:
    _committed(session, lambda: delete_user_ingredient(session, entry_id))
    return Response(status_code=204)


@router.get("/cookware")
def search_cookware(
    session: SessionDep, q: str | None = None, source: Source | None = None, limit: Limit = None
) -> list[CookwareOut]:
    return _cookware_outs(session, _search(session, Cookware, q, source, limit))


@router.post("/cookware", status_code=201)
def create_cookware(data: CookwareIn, session: SessionDep) -> CookwareOut:
    """A new entry only records that a dish needs it; `owned` adds it to the user's kitchen."""

    def create() -> CookwareOut:
        entry = create_user_cookware(session, CookwareWrite(**data.model_dump(exclude={"owned"})))
        if data.owned:
            session.add(CookwareOwned(cookware_id=entry.id))
        session.flush()
        return _cookware_outs(session, [entry])[0]

    return _committed(session, create)


@router.patch("/cookware/{entry_id}")
def patch_cookware(entry_id: int, patch: DictionaryPatch, session: SessionDep) -> CookwareOut:
    return _committed(
        session,
        lambda: _cookware_outs(session, [update_user_cookware(session, entry_id, patch)])[0],
    )


@router.delete("/cookware/{entry_id}", status_code=204)
def delete_cookware(entry_id: int, session: SessionDep) -> Response:
    _committed(session, lambda: delete_user_cookware(session, entry_id))
    return Response(status_code=204)


@router.post("/allergen-suggestions")
def allergen_suggestions(body: SuggestionIn, client: ClientDep) -> SuggestionOut:
    return SuggestionOut(suggested=suggest_allergens(client, body.name))


@router.get("/allergens/unresolved")
def list_unresolved(session: SessionDep) -> UnresolvedOut:
    entries, names = unresolved_allergens(session)
    return UnresolvedOut(
        entries=[UnresolvedEntryOut(**vars(entry)) for entry in entries],
        unmatched=[UnresolvedNameOut(**vars(name)) for name in names],
    )


@router.post("/allergens/resolve")
def resolve(body: ResolveIn, session: SessionDep) -> LinkedIngredient:
    def apply() -> LinkedIngredient:
        entry, linked = resolve_allergens(session, body.name, body.ingredient_id, body.allergens)
        return _linked(session, entry, linked)

    return _committed(session, apply)


@router.post("/allergens/link")
def link(body: LinkIn, session: SessionDep) -> LinkedByName:
    def apply() -> LinkedByName:
        entry, linked, remembered = link_name(
            session, body.name, body.ingredient_id, body.remember
        )
        return LinkedByName(**_linked(session, entry, linked).model_dump(), remembered=remembered)

    return _committed(session, apply)
