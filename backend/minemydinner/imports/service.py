"""Turn an import into a draft. Nothing here writes a recipe: the user confirms first."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from minemydinner.constants import SOURCE_TYPES
from minemydinner.extraction.extractor import (
    ExtractionResult,
    ImageInput,
    extract_from_images,
    extract_from_text,
)
from minemydinner.extraction.normalize import match_cookware, match_ingredient
from minemydinner.extraction.schemas import ExtractedIngredient, ExtractedRecipe
from minemydinner.imports.batch import split_batch
from minemydinner.imports.web import WebImportError, fetch_page_text
from minemydinner.llm.client import AllModelsFailed, LLMClient, MonthlyCapExceeded
from minemydinner.models import Ingredient

logger = logging.getLogger(__name__)

# Long enough for any recipe, short enough to bound the cost of one call.
MAX_TEXT_CHARS = 20_000
SCREENSHOT_INPUT = "[screenshot]"
BATCH_WORKERS = 4
_URL_BODY = r"[^\s<>\"'，。；！？、）】」]"
# Scheme-less links stop at the first CJK character, so a link pasted against Chinese text
# does not swallow it.
_BARE_BODY = r"[^\s<>\"'，。；！？、）】」\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]"
_NOT_AFTER = r"(?<![A-Za-z0-9.@/_-])"
# A scheme, a www. prefix, or a bare host.tld/path. A bare host starts with an ASCII letter
# and has a TLD of 2+ ASCII letters followed by a slash, which keeps 1.5/2, v1.2, 2.5kg/份,
# and e-mail addresses out.
URL_PATTERN = re.compile(
    rf"https?://{_URL_BODY}+"
    rf"|{_NOT_AFTER}www\.{_BARE_BODY}+"
    rf"|{_NOT_AFTER}[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    rf"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{{2,}}/{_BARE_BODY}*"
)
TRAILING_PUNCTUATION = ".,;:!?)]}'\""


class DraftIngredient(ExtractedIngredient):
    ingredient_id: int | None


class DraftLine(DraftIngredient):
    """A line as the review form shows it. The linked entry's own names are for display
    only; a line's name keeps the source wording, such as 葱花 linked to 葱."""

    linked_name_zh: str | None = None
    linked_name_en: str | None = None

    @classmethod
    def of(cls, line: ExtractedIngredient, entry: Ingredient | None) -> "DraftLine":
        return cls(
            **line.model_dump(include=set(ExtractedIngredient.model_fields)),
            ingredient_id=entry.id if entry else None,
            linked_name_zh=entry.name_zh if entry else None,
            linked_name_en=entry.name_en if entry else None,
        )


class DraftCookware(BaseModel):
    name: str
    cookware_id: int | None


class Provenance(BaseModel):
    input_text: str
    model_output: dict[str, Any]
    model_name: str
    prompt_version: str


class RecipeDraft(ExtractedRecipe):
    ingredients: list[DraftLine]
    cookware: list[DraftCookware]
    source_type: Literal[*SOURCE_TYPES]
    source_url: str | None
    provenance: Provenance


class BatchItem(BaseModel):
    index: int
    draft: RecipeDraft | None
    error: str | None


def _first_link(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    return match.group(0).rstrip(TRAILING_PUNCTUATION) if match else None


def _with_scheme(link: str) -> str:
    return link if link.startswith(("http://", "https://")) else f"https://{link}"


def first_url(text: str) -> str | None:
    """The first link, with https:// prepended when it was written without a scheme."""
    link = _first_link(text)
    return _with_scheme(link) if link else None


def single_url(text: str) -> str | None:
    """The URL when the whole trimmed text is one link."""
    text = text.strip()
    link = _first_link(text)
    return _with_scheme(link) if link == text else None


def build_draft(
    session: Session,
    result: ExtractionResult,
    source_type: str,
    source_url: str | None,
    input_text: str,
) -> RecipeDraft:
    recipe = result.recipe
    ingredients = []
    for line in recipe.ingredients:
        ingredients.append(DraftLine.of(line, match_ingredient(session, line.name)))
    cookware = []
    for name in recipe.cookware:
        match = match_cookware(session, name)
        cookware.append(DraftCookware(name=name, cookware_id=match.id if match else None))
    return RecipeDraft(
        **recipe.model_dump(exclude={"ingredients", "cookware"}),
        ingredients=ingredients,
        cookware=cookware,
        source_type=source_type,
        source_url=source_url,
        provenance=Provenance(
            input_text=input_text,
            model_output=result.raw_output,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
        ),
    )


def draft_from_url(session: Session, client: LLMClient, url: str) -> RecipeDraft:
    url = url.strip()
    page_text = fetch_page_text(url)
    if len(page_text) > MAX_TEXT_CHARS:
        raise WebImportError("too_large")
    return build_draft(session, extract_from_text(client, page_text), "web", url, page_text)


def draft_from_text(session: Session, client: LLMClient, text: str) -> RecipeDraft:
    # One trim at the entry point: the model, the amount rules, and the stored
    # provenance all see the same text, without padding around the paste.
    text = text.strip()
    url = single_url(text)
    if url is not None:
        return draft_from_url(session, client, url)
    result = extract_from_text(client, text)
    return build_draft(session, result, "text", first_url(text), text)


def draft_from_images(
    session: Session, client: LLMClient, images: list[ImageInput]
) -> RecipeDraft:
    result = extract_from_images(client, images)
    return build_draft(session, result, "screenshot", None, SCREENSHOT_INPUT)


def error_code(error: Exception) -> str:
    if isinstance(error, MonthlyCapExceeded):
        return "monthly_cap"
    if isinstance(error, AllModelsFailed):
        return "models_unavailable"
    return "extraction_failed"


def drafts_from_batch(session: Session, client: LLMClient, data: bytes) -> list[BatchItem]:
    """Extract every dish in a batch file. One failed dish does not lose the others.

    Links in a batch file are kept as sources but never fetched, so there is no
    batch scraping.
    """
    texts = split_batch(data)

    def extract(text: str) -> ExtractionResult | Exception:
        try:
            return extract_from_text(client, text)
        except Exception as error:
            if not isinstance(error, MonthlyCapExceeded | AllModelsFailed):
                logger.exception("Batch item extraction failed")
            return error

    # Model calls run in parallel; dictionary matching uses the session, so it stays here.
    with ThreadPoolExecutor(max_workers=BATCH_WORKERS) as pool:
        results = list(pool.map(extract, texts))
    items = []
    for index, (text, result) in enumerate(zip(texts, results, strict=True)):
        if isinstance(result, Exception):
            items.append(BatchItem(index=index, draft=None, error=error_code(result)))
        else:
            draft = build_draft(session, result, "batch", first_url(text), text)
            items.append(BatchItem(index=index, draft=draft, error=None))
    return items
