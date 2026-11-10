from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from minemydinner.api.deps import ApiError, contains_pattern, get_llm_client
from minemydinner.constants import DISH_TYPES
from minemydinner.db import get_session
from minemydinner.extraction.schemas import ExtractedIngredient
from minemydinner.imports.service import DraftCookware, DraftLine
from minemydinner.llm.client import LLMClient
from minemydinner.extraction.extractor import ImageInput
from minemydinner.models import (
    Cookware,
    Diner,
    ExtractionLog,
    Ingredient,
    Recipe,
    RecipeIngredient,
)
from minemydinner.recipes.allergy import (
    LineRef,
    find_allergy_conflicts,
    find_unknown_allergen_lines,
)
from minemydinner.recipes.embedding import retry_pending_embeddings
from minemydinner.recipes.images import (
    EXTENSIONS,
    MAX_IMAGE_BYTES,
    TooManyImages,
    image_path,
    mime_type_of,
    store_images,
)
from minemydinner.recipes.service import (
    InvalidRecipe,
    RecipeWrite,
    create_recipe,
    delete_recipe,
    update_recipe,
)
from minemydinner.settings import Settings, get_settings

router = APIRouter(prefix="/api/recipes")
images_router = APIRouter(prefix="/api/images")

PAGE_SIZE = 20

SessionDep = Annotated[Session, Depends(get_session)]
ClientDep = Annotated[LLMClient, Depends(get_llm_client)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class CreatedRecipe(BaseModel):
    id: int
    embedded: bool
    # The extraction log written with the recipe, so its screenshots can be added to it.
    log_id: int | None


class ImageOut(BaseModel):
    file_name: str
    mime_type: str
    position: int


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


class RecipeOut(BaseModel):
    id: int
    lang: str
    title: str
    dish_type: str
    time_min: int | None
    time_source: str | None
    servings: int | None
    tags: list[str]
    supplies: list[str]
    steps_summary: str | None
    ingredients: list[DraftLine]
    # Named in the recipe's language, so the review form can show them.
    cookware: list[DraftCookware]
    cookware_ids: list[int]
    source_type: str
    source_url: str | None
    source_text: str | None
    images: list[ImageOut]
    embedded: bool
    created_at: datetime


class RecipePage(BaseModel):
    items: list[RecipeOut]
    total: int
    page: int


def _cookware_name(cookware: Cookware, lang: str) -> str:
    preferred = cookware.name_zh if lang == "zh" else cookware.name_en
    return preferred or cookware.name_zh or cookware.name_en or ""


def _recipe_out(recipe: Recipe) -> RecipeOut:
    return RecipeOut(
        id=recipe.id,
        lang=recipe.lang,
        title=recipe.title,
        dish_type=recipe.dish_type,
        time_min=recipe.time_min,
        time_source=recipe.time_source,
        servings=recipe.servings,
        tags=recipe.tags,
        supplies=recipe.supplies,
        steps_summary=recipe.steps_summary,
        ingredients=[
            DraftLine.of(
                ExtractedIngredient.model_validate(line, from_attributes=True), line.ingredient
            )
            for line in recipe.ingredients
        ],
        cookware=[
            DraftCookware(
                name=_cookware_name(item.cookware, recipe.lang), cookware_id=item.cookware_id
            )
            for item in recipe.cookware
        ],
        cookware_ids=[item.cookware_id for item in recipe.cookware],
        source_type=recipe.source_type,
        source_url=recipe.source_url,
        source_text=recipe.source_text,
        images=[
            ImageOut(file_name=image.file_name, mime_type=image.mime_type, position=image.position)
            for image in recipe.images
        ],
        embedded=recipe.embedding is not None,
        created_at=recipe.created_at,
    )


def _not_found(error: InvalidRecipe) -> ApiError:
    return ApiError(404 if error.code == "not_found" else 422, error.code)


@router.get("")
def list_recipes(
    session: SessionDep,
    q: str | None = None,
    dish_type: Literal[*DISH_TYPES] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> RecipePage:
    """Text search on title, tags, and ingredient names, with no model call."""
    query = select(Recipe)
    if q and q.strip():
        pattern = contains_pattern(q)
        line_matches = (
            select(RecipeIngredient.id)
            .outerjoin(Ingredient, RecipeIngredient.ingredient_id == Ingredient.id)
            .where(
                RecipeIngredient.recipe_id == Recipe.id,
                or_(
                    RecipeIngredient.name.ilike(pattern),
                    # Dictionary names let "egg" find a recipe whose line says "鸡蛋".
                    Ingredient.name_zh.ilike(pattern),
                    Ingredient.name_en.ilike(pattern),
                ),
            )
        )
        query = query.where(
            or_(
                Recipe.title.ilike(pattern),
                func.array_to_string(Recipe.tags, "\n").ilike(pattern),
                exists(line_matches),
            )
        )
    if dish_type is not None:
        query = query.where(Recipe.dish_type == dish_type)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(Recipe.created_at.desc(), Recipe.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return RecipePage(items=[_recipe_out(row) for row in rows], total=total, page=page)


@router.get("/{recipe_id}")
def get_recipe(recipe_id: int, session: SessionDep) -> RecipeOut:
    recipe = session.get(Recipe, recipe_id)
    if recipe is None:
        raise ApiError(404, "not_found")
    return _recipe_out(recipe)


@router.put("/{recipe_id}")
def put_recipe(
    recipe_id: int, data: RecipeWrite, session: SessionDep, client: ClientDep
) -> RecipeOut:
    """An edit replaces the lines and recomputes the embedding. Edits write no
    extraction log, so the log keeps only corrections of model output."""
    try:
        recipe = update_recipe(session, client, recipe_id, data)
    except InvalidRecipe as error:
        raise _not_found(error) from error
    return _recipe_out(recipe)


@router.delete("/{recipe_id}", status_code=204)
def remove_recipe(recipe_id: int, session: SessionDep, settings: SettingsDep) -> Response:
    try:
        delete_recipe(session, recipe_id, settings.image_dir)
    except InvalidRecipe as error:
        raise _not_found(error) from error
    return Response(status_code=204)


@router.post("", status_code=201)
def save_recipe(
    data: RecipeWrite, session: SessionDep, client: ClientDep, settings: SettingsDep
) -> CreatedRecipe:
    try:
        recipe, log = create_recipe(session, client, data, settings.extraction_log_max_rows)
    except InvalidRecipe as error:
        raise ApiError(422, error.code) from error
    return CreatedRecipe(
        id=recipe.id, embedded=recipe.embedding is not None, log_id=log.id if log else None
    )


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


def _read_image(upload: UploadFile) -> ImageInput:
    if upload.content_type not in EXTENSIONS:
        raise ApiError(415, "unsupported_image_type")
    data = upload.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, "file_too_large")
    return ImageInput(upload.content_type, data)


@router.post("/{recipe_id}/images", status_code=201)
def upload_images(
    recipe_id: int,
    images: list[UploadFile],
    session: SessionDep,
    settings: SettingsDep,
    log_id: Annotated[int | None, Form()] = None,
) -> list[ImageOut]:
    """Keep a saved recipe's screenshots, after the user confirmed the draft."""
    recipe = session.get(Recipe, recipe_id)
    if recipe is None:
        raise ApiError(404, "not_found")
    inputs = [_read_image(image) for image in images]
    log = session.get(ExtractionLog, log_id) if log_id is not None else None
    try:
        added = store_images(session, recipe, inputs, settings.image_dir, log)
    except TooManyImages as error:
        raise ApiError(422, "image_count") from error
    return [
        ImageOut(file_name=row.file_name, mime_type=row.mime_type, position=row.position)
        for row in added
    ]


@images_router.get("/{file_name}")
def get_image(file_name: str, settings: SettingsDep) -> FileResponse:
    path = image_path(settings.image_dir, file_name)
    if path is None:
        raise ApiError(404, "not_found")
    return FileResponse(path, media_type=mime_type_of(file_name))
