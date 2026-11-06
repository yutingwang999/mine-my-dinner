from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from minemydinner.api.deps import ApiError, get_llm_client
from minemydinner.db import get_session
from minemydinner.extraction.extractor import ImageInput
from minemydinner.imports.batch import MAX_BATCH_BYTES, BatchFileError
from minemydinner.imports.service import (
    MAX_TEXT_CHARS,
    BatchItem,
    RecipeDraft,
    draft_from_images,
    draft_from_text,
    draft_from_url,
    drafts_from_batch,
)
from minemydinner.imports.web import WebImportError
from minemydinner.llm.client import LLMClient

router = APIRouter(prefix="/api/imports")

MAX_IMAGES = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

SessionDep = Annotated[Session, Depends(get_session)]
ClientDep = Annotated[LLMClient, Depends(get_llm_client)]


class TextImport(BaseModel):
    text: str


class UrlImport(BaseModel):
    url: str


class BatchResult(BaseModel):
    items: list[BatchItem]


def _check_text(text: str) -> None:
    if not text.strip():
        raise ApiError(422, "empty_text")
    if len(text) > MAX_TEXT_CHARS:
        raise ApiError(413, "text_too_large")


def _web_error(error: WebImportError) -> ApiError:
    return ApiError(422, error.reason, suggest_paste=True)


@router.post("/text")
def import_text(body: TextImport, session: SessionDep, client: ClientDep) -> RecipeDraft:
    _check_text(body.text)
    try:
        return draft_from_text(session, client, body.text)
    except WebImportError as error:
        raise _web_error(error) from error


@router.post("/url")
def import_url(body: UrlImport, session: SessionDep, client: ClientDep) -> RecipeDraft:
    try:
        return draft_from_url(session, client, body.url)
    except WebImportError as error:
        raise _web_error(error) from error


def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(413, "file_too_large")
    return data


@router.post("/screenshots")
def import_screenshots(
    images: list[UploadFile], session: SessionDep, client: ClientDep
) -> RecipeDraft:
    if not 1 <= len(images) <= MAX_IMAGES:
        raise ApiError(422, "image_count")
    for image in images:
        if image.content_type not in IMAGE_TYPES:
            raise ApiError(415, "unsupported_image_type")
    # Image bytes stay in memory for this request only and are never stored.
    inputs = [
        ImageInput(image.content_type, _read_limited(image, MAX_IMAGE_BYTES)) for image in images
    ]
    return draft_from_images(session, client, inputs)


@router.post("/batch")
def import_batch(file: UploadFile, session: SessionDep, client: ClientDep) -> BatchResult:
    data = _read_limited(file, MAX_BATCH_BYTES)
    try:
        return BatchResult(items=drafts_from_batch(session, client, data))
    except BatchFileError as error:
        raise ApiError(422, error.reason) from error
