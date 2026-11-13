import pytest

from minemydinner.llm.client import MonthlyCapExceeded
from minemydinner.models import Recipe, RecipeIngredient
from minemydinner.recipes.embedding import embedding_text, retry_pending_embeddings
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import FakeLLMClient, saved


def make_recipe(session, title: str, embedded: bool) -> Recipe:
    recipe = Recipe(
        lang="zh",
        title=title,
        dish_type="vegetable dish",
        tags=["快手"],
        steps_summary="炒熟即可。",
        source_type="manual",
        embedding=[0.2] * 768 if embedded else None,
        ingredients=[
            RecipeIngredient(raw="两个鸡蛋", name="鸡蛋", amount_type="count", role="main")
        ],
    )
    session.add(recipe)
    session.flush()
    return recipe


@pytest.fixture
def recipe_pending(session):
    return make_recipe(session, "番茄炒蛋", embedded=False)


@pytest.fixture
def recipe_done(session):
    return make_recipe(session, "土豆丝", embedded=True)


def test_embedding_text_has_title_names_tags_and_summary(session, recipe_pending):
    text = embedding_text(recipe_pending)
    for part in ("番茄炒蛋", "鸡蛋", "快手", "炒熟即可。"):
        assert part in text
    assert "两个鸡蛋" not in text


def test_failed_embedding_keeps_recipe_for_retry(client, session, seeded, llm_embed_down, recipe_write):
    assert saved(client, session, recipe_write).embedding is None


def test_cap_reached_keeps_recipe_for_retry(client, session, seeded, llm_over_cap, recipe_write):
    assert saved(client, session, recipe_write).embedding is None


def test_retry_fills_only_pending(session, recipe_pending, recipe_done):
    llm = FakeLLMClient()
    assert retry_pending_embeddings(session, llm) == 1
    assert recipe_pending.embedding is not None
    assert len(llm.embedded) == 1


def test_retry_stops_at_the_cap(session, recipe_pending):
    make_recipe(session, "土豆丝", embedded=False)
    assert retry_pending_embeddings(session, FakeLLMClient(embed_error=MonthlyCapExceeded("x"))) == 0


def test_retry_route(client, session, recipe_pending, fake_llm):
    assert client.post("/api/recipes/embeddings/retry").json() == {"embedded": 1}
