from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from minemydinner.api.deps import ApiError, get_llm_client
from minemydinner.db import get_session
from minemydinner.dictionary.service import (
    CookwareWrite,
    HierarchyError,
    IngredientWrite,
    TermConflict,
    create_user_cookware,
    create_user_ingredient,
    link_unmatched_lines,
)
from minemydinner.extraction.allergen_suggest import suggest_allergens
from minemydinner.llm.client import LLMClient
from minemydinner.models import Cookware, CookwareOwned, Ingredient

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]
ClientDep = Annotated[LLMClient, Depends(get_llm_client)]
SEARCH_LIMIT = 20


class IngredientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name_zh: str | None
    name_en: str | None
    aliases_zh: list[str]
    aliases_en: list[str]
    category: str
    stock_mode: str
    allergens: list[str] | None
    parent_id: int | None
    source: str


class CreatedIngredient(IngredientOut):
    linked_lines: int


class CookwareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name_zh: str | None
    name_en: str | None
    aliases_zh: list[str]
    aliases_en: list[str]
    source: str


class CookwareIn(CookwareWrite):
    owned: bool


class SuggestionIn(BaseModel):
    name: str


class SuggestionOut(BaseModel):
    suggested: list[str] | None


def _search(session: Session, table: type, q: str | None) -> list:
    query = select(table).order_by(table.id).limit(SEARCH_LIMIT)
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(
            or_(
                table.name_zh.ilike(pattern),
                table.name_en.ilike(pattern),
                func.array_to_string(table.aliases_zh, "\n").ilike(pattern),
                func.array_to_string(table.aliases_en, "\n").ilike(pattern),
            )
        )
    return list(session.scalars(query))


def _conflict(error: TermConflict) -> ApiError:
    return ApiError(409, "term_conflict", extra={"term": error.term, "owner_id": error.owner_id})


@router.get("/ingredients")
def search_ingredients(session: SessionDep, q: str | None = None) -> list[IngredientOut]:
    return [IngredientOut.model_validate(row) for row in _search(session, Ingredient, q)]


@router.post("/ingredients", status_code=201)
def create_ingredient(data: IngredientWrite, session: SessionDep) -> CreatedIngredient:
    try:
        entry = create_user_ingredient(session, data)
    except TermConflict as error:
        raise _conflict(error) from error
    except HierarchyError as error:
        raise ApiError(422, "invalid_parent") from error
    linked = link_unmatched_lines(session, entry)
    session.commit()
    created = IngredientOut.model_validate(entry).model_dump()
    return CreatedIngredient(**created, linked_lines=linked)


@router.get("/cookware")
def search_cookware(session: SessionDep, q: str | None = None) -> list[CookwareOut]:
    return [CookwareOut.model_validate(row) for row in _search(session, Cookware, q)]


@router.post("/cookware", status_code=201)
def create_cookware(data: CookwareIn, session: SessionDep) -> CookwareOut:
    """A new entry only records that a dish needs it; `owned` adds it to the user's kitchen."""
    try:
        entry = create_user_cookware(session, CookwareWrite(**data.model_dump(exclude={"owned"})))
    except TermConflict as error:
        raise _conflict(error) from error
    if data.owned:
        session.add(CookwareOwned(cookware_id=entry.id))
    session.commit()
    return CookwareOut.model_validate(entry)


@router.post("/allergen-suggestions")
def allergen_suggestions(body: SuggestionIn, client: ClientDep) -> SuggestionOut:
    return SuggestionOut(suggested=suggest_allergens(client, body.name))
