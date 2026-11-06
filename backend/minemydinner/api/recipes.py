from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.api.deps import ApiError, get_llm_client
from minemydinner.db import get_session
from minemydinner.llm.client import LLMClient
from minemydinner.models import Diner
from minemydinner.recipes.allergy import (
    LineRef,
    find_allergy_conflicts,
    find_unknown_allergen_lines,
)
from minemydinner.recipes.embedding import retry_pending_embeddings
from minemydinner.recipes.service import InvalidRecipe, RecipeWrite, create_recipe
from minemydinner.settings import Settings, get_settings

router = APIRouter(prefix="/api/recipes")

SessionDep = Annotated[Session, Depends(get_session)]
ClientDep = Annotated[LLMClient, Depends(get_llm_client)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class CreatedRecipe(BaseModel):
    id: int
    embedded: bool


class LineIn(BaseModel):
    name: str
    ingredient_id: int | None


class AllergyCheckIn(BaseModel):
    lines: list[LineIn]


class ConflictOut(BaseModel):
    line_name: str
    allergen: str | None
    ingredient_id: int | None


class AllergyCheckOut(BaseModel):
    conflicts: list[ConflictOut]
    unknown: list[str]


class RetryOut(BaseModel):
    embedded: int


@router.post("", status_code=201)
def save_recipe(
    data: RecipeWrite, session: SessionDep, client: ClientDep, settings: SettingsDep
) -> CreatedRecipe:
    try:
        recipe = create_recipe(session, client, data, settings.extraction_log_max_rows)
    except InvalidRecipe as error:
        raise ApiError(422, error.code) from error
    return CreatedRecipe(id=recipe.id, embedded=recipe.embedding is not None)


@router.post("/allergy-check")
def allergy_check(body: AllergyCheckIn, session: SessionDep) -> AllergyCheckOut:
    """Warn about the user's own allergies at import. This never blocks saving."""
    lines = [LineRef(line.name, line.ingredient_id) for line in body.lines]
    self_ids = list(session.scalars(select(Diner.id).where(Diner.is_self)))
    conflicts = find_allergy_conflicts(session, self_ids, lines)
    return AllergyCheckOut(
        conflicts=[
            ConflictOut(
                line_name=item.line_name, allergen=item.allergen, ingredient_id=item.ingredient_id
            )
            for item in conflicts
        ],
        unknown=find_unknown_allergen_lines(session, lines),
    )


@router.post("/embeddings/retry")
def retry_embeddings(session: SessionDep, client: ClientDep) -> RetryOut:
    return RetryOut(embedded=retry_pending_embeddings(session, client))
