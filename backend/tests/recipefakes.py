import copy

import pytest
from sqlalchemy import func, select

from minemydinner.api.deps import get_llm_client
from minemydinner.constants import EMBEDDING_DIMENSION
from minemydinner.llm.client import AllModelsFailed, MonthlyCapExceeded, StructuredResult
from minemydinner.main import app
from minemydinner.models import Cookware, Ingredient, Recipe
from minemydinner.seed import seed_all


class FakeLLMClient:
    """Stands in for LLMClient in recipe tests."""

    def __init__(self, payload=None, error=None, embed_error=None):
        self.payload = payload
        self.error = error
        self.embed_error = embed_error
        self.embedded: list[str] = []

    def invoke_structured(self, task, messages, schema):
        if self.error is not None:
            raise self.error
        return StructuredResult(schema.model_validate(self.payload), "gemini", False)

    def embed(self, texts, task="embeddings"):
        if self.embed_error is not None:
            raise self.embed_error
        self.embedded.extend(texts)
        return [[0.1] * EMBEDDING_DIMENSION for _ in texts]


def use_llm(llm: FakeLLMClient) -> FakeLLMClient:
    app.dependency_overrides[get_llm_client] = lambda: llm
    return llm


@pytest.fixture
def seeded(session):
    seed_all(session)
    session.flush()
    return session


def by_zh(session, name: str) -> Ingredient:
    return session.scalars(select(Ingredient).where(Ingredient.name_zh == name)).one()


def cookware_id(session, name: str) -> int:
    return session.scalars(select(Cookware.id).where(Cookware.name_zh == name)).one()


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def one(session, model):
    return session.scalars(select(model)).one()


@pytest.fixture
def fake_llm(client):
    return use_llm(FakeLLMClient())


@pytest.fixture
def llm_embed_down(client):
    return use_llm(FakeLLMClient(embed_error=AllModelsFailed("down")))


@pytest.fixture
def llm_over_cap(client):
    return use_llm(FakeLLMClient(embed_error=MonthlyCapExceeded("over")))


def line(raw, name, ingredient_id, **fields):
    return {
        "raw": raw,
        "name": name,
        "ingredient_id": ingredient_id,
        "amount": None,
        "unit": None,
        "amount_type": "vague",
        "role": "secondary",
        "optional": False,
    } | fields


@pytest.fixture
def recipe_write(seeded):
    return {
        "lang": "zh",
        "title": "葱花蛋",
        "dish_type": "vegetable dish",
        "time_min": 10,
        "time_source": "stated",
        "servings": 2,
        "tags": ["快手"],
        "steps_summary": "鸡蛋炒熟后撒葱花。",
        "ingredients": [
            line("少量葱花", "葱花", by_zh(seeded, "葱").id),
            line("秘制酱", "秘制酱", None, amount_type="missing", role="seasoning"),
        ],
        "cookware_ids": [cookware_id(seeded, "炒锅")],
        "source_type": "web",
        "source_url": "https://recipes.example/1",
        "provenance": {
            "input_text": "葱花蛋：少量葱花，秘制酱",
            "model_output": {"title": "葱花蛋", "ingredients": [{"amount": None}]},
            "model_name": "gemini",
            "prompt_version": "extract_v1",
        },
    }


@pytest.fixture
def recipe_write_with_invented_amount(recipe_write):
    data = copy.deepcopy(recipe_write)
    data["provenance"]["model_output"]["ingredients"][0]["amount"] = 5
    return data


@pytest.fixture
def recipe_write_with_haimi(recipe_write, seeded):
    data = copy.deepcopy(recipe_write)
    data["ingredients"].append(line("海米10克", "海米", by_zh(seeded, "海米").id))
    return data


def saved(client, session, data) -> Recipe:
    response = client.post("/api/recipes", json=data)
    assert response.status_code == 201, response.text
    return session.get(Recipe, response.json()["id"])


def lines(data) -> dict:
    return {
        "lines": [
            {"name": item["name"], "ingredient_id": item["ingredient_id"]}
            for item in data["ingredients"]
        ]
    }
