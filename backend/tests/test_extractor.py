import base64

import pytest
from langchain_core.messages import SystemMessage

from minemydinner.extraction.extractor import (
    PROMPT_VERSION,
    ImageInput,
    extract_from_images,
    extract_from_text,
)
from minemydinner.llm.client import StructuredResult

INVENTED_AMOUNT = {
    "lang": "zh",
    "title": "葱花蛋",
    "dish_type": "vegetable dish",
    "time_min": None,
    "time_source": None,
    "servings": None,
    "tags": [],
    "steps_summary": None,
    "ingredients": [
        {
            "raw": "少量葱花",
            "name": "葱花",
            "amount": 5,
            "unit": "g",
            "amount_type": "exact",
            "role": "seasoning",
            "optional": False,
        }
    ],
    "cookware": [],
}


class FakeLLM:
    """Stands in for LLMClient and records the last request."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_task = None
        self.last_text = None
        self.last_system_text = None
        self.last_has_image = False
        self.last_image_url = None

    def invoke_structured(self, task, messages, schema):
        self.last_task = task
        for message in messages:
            if isinstance(message, SystemMessage):
                self.last_system_text = message.content
            elif isinstance(message.content, str):
                self.last_text = message.content
            else:
                for part in message.content:
                    if part["type"] == "text":
                        self.last_text = part["text"]
                    else:
                        self.last_has_image = True
                        self.last_image_url = part["image_url"]["url"]
        return StructuredResult(schema.model_validate(self.payload), "gemini", False)


@pytest.fixture
def fake_llm():
    return FakeLLM(INVENTED_AMOUNT)


@pytest.fixture
def fake_llm_returning_invented_amount():
    return FakeLLM(INVENTED_AMOUNT)


def test_raw_output_keeps_invented_amount_but_recipe_does_not(fake_llm_returning_invented_amount):
    result = extract_from_text(fake_llm_returning_invented_amount, "少量葱花")
    assert result.raw_output["ingredients"][0]["amount"] == 5
    assert result.recipe.ingredients[0].amount is None
    assert result.recipe.ingredients[0].amount_type == "vague"


def test_result_records_model_and_prompt_version(fake_llm):
    result = extract_from_text(fake_llm, "番茄炒蛋")
    assert (result.model_name, result.prompt_version) == ("gemini", PROMPT_VERSION)
    assert PROMPT_VERSION == "extract_v1"


def test_prompt_contains_version_rules_and_input(fake_llm):
    extract_from_text(fake_llm, "番茄炒蛋")
    assert fake_llm.last_task == "extraction"
    assert "番茄炒蛋" in fake_llm.last_text and "must not" in fake_llm.last_system_text


def test_images_use_image_task_and_pass_image_parts(fake_llm):
    extract_from_images(fake_llm, [ImageInput("image/png", b"x")])
    assert fake_llm.last_task == "image_understanding" and fake_llm.last_has_image
    expected = "data:image/png;base64," + base64.b64encode(b"x").decode()
    assert fake_llm.last_image_url == expected


def test_images_require_at_least_one_image(fake_llm):
    with pytest.raises(ValueError):
        extract_from_images(fake_llm, [])


def test_prompt_leaves_out_basic_utensils_and_names_the_kind_of_pot():
    from minemydinner.extraction.extractor import load_prompt

    prompt = load_prompt()
    for utensil in ("chopsticks", "spatulas", "knives", "cutting boards"):
        assert utensil in prompt
    assert "must not list basic utensils" in prompt
    assert '"锅"' in prompt and "leave it out" in prompt


def test_prompt_cleans_numbered_titles_and_names_untitled_recipes():
    from minemydinner.extraction.extractor import load_prompt

    prompt = load_prompt()
    assert "`title`" in prompt and "numbering" in prompt and "■" in prompt
    assert "no title" in prompt


def test_prompt_states_the_design_rules():
    from minemydinner.extraction.extractor import load_prompt

    prompt = load_prompt()
    for rule in ("raw", "estimated", "meat dish", "staple food", "生抽", "steps_summary"):
        assert rule in prompt
