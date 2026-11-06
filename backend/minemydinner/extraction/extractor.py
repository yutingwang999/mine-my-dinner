import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from minemydinner.extraction.postprocess import enforce_amount_rules
from minemydinner.extraction.schemas import ExtractedRecipe
from minemydinner.llm.client import LLMClient

PROMPT_VERSION = "extract_v1"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
IMAGE_INSTRUCTION = "Extract the recipe shown in these images."


@dataclass(frozen=True)
class ImageInput:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class ExtractionResult:
    recipe: ExtractedRecipe
    # The model's result before enforce_amount_rules, kept to measure fabrication.
    raw_output: dict[str, Any]
    model_name: str
    prompt_version: str


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return (PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")


def _extract(client: LLMClient, task: str, message: HumanMessage) -> ExtractionResult:
    messages: list[BaseMessage] = [SystemMessage(load_prompt()), message]
    result = client.invoke_structured(task, messages, ExtractedRecipe)
    return ExtractionResult(
        recipe=enforce_amount_rules(result.parsed),
        raw_output=result.parsed.model_dump(),
        model_name=result.model_name,
        prompt_version=PROMPT_VERSION,
    )


def extract_from_text(client: LLMClient, text: str) -> ExtractionResult:
    return _extract(client, "extraction", HumanMessage(f"Recipe text:\n\n{text}"))


def extract_from_images(client: LLMClient, images: list[ImageInput]) -> ExtractionResult:
    if not images:
        raise ValueError("At least one image is needed")
    parts: list[str | dict] = [{"type": "text", "text": IMAGE_INSTRUCTION}]
    for image in images:
        encoded = base64.b64encode(image.data).decode()
        url = f"data:{image.mime_type};base64,{encoded}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return _extract(client, "image_understanding", HumanMessage(parts))
