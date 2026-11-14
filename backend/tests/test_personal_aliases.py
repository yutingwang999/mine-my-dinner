import pytest
from sqlalchemy import select

from minemydinner.dictionary.service import (
    ReadOnlyEntry,
    TermConflict,
    set_personal_aliases,
)
from minemydinner.extraction.normalize import all_terms, match_ingredient
from minemydinner.models import Ingredient, Recipe, RecipeIngredient
from minemydinner.seed import seed_all
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import by_zh

USER_ENTRY = {"category": "seasoning", "stock_mode": "level", "name_zh": "秘制酱"}


@pytest.fixture
def recipe_with_unlinked_line(session, seeded):
    recipe = Recipe(
        lang="zh",
        title="测试菜",
        dish_type="other",
        source_type="manual",
        ingredients=[
            RecipeIngredient(
                raw="马铃薯丁", name="马铃薯丁", amount_type="missing", role="main"
            )
        ],
    )
    session.add(recipe)
    session.flush()
    return recipe


def unlinked_line(session) -> RecipeIngredient:
    return session.scalars(
        select(RecipeIngredient).where(RecipeIngredient.name == "马铃薯丁")
    ).one()


@pytest.fixture
def user_ingredient(session, seeded):
    entry = Ingredient(name_zh="外婆辣酱", category="seasoning", stock_mode="level", source="user")
    session.add(entry)
    session.flush()
    return entry


def test_personal_alias_links_new_import_lines(session, seeded):
    potato = by_zh(session, "土豆")
    set_personal_aliases(session, potato.id, ["马铃薯丁"], [])
    assert match_ingredient(session, "马铃薯丁").id == potato.id


def test_personal_alias_relinks_existing_unlinked_lines(
    client, session, recipe_with_unlinked_line
):
    potato = by_zh(session, "土豆")
    entry, relinked = set_personal_aliases(session, potato.id, ["马铃薯丁"], [])
    session.expire_all()
    assert relinked == 1 and unlinked_line(session).ingredient_id == potato.id


def test_personal_alias_survives_reseed(session, seeded):
    set_personal_aliases(session, by_zh(session, "土豆").id, ["马铃薯丁"], [])
    seed_all(session)
    session.flush()
    session.expire_all()
    assert by_zh(session, "土豆").user_aliases_zh == ["马铃薯丁"]


def test_personal_alias_conflicting_with_another_entry_is_rejected(session, seeded):
    with pytest.raises(TermConflict):
        set_personal_aliases(session, by_zh(session, "土豆").id, ["番茄"], [])


def test_user_entry_rejects_personal_aliases(session, user_ingredient):
    with pytest.raises(ReadOnlyEntry):
        set_personal_aliases(session, user_ingredient.id, ["x"], [])


def test_new_user_entry_cannot_take_a_personal_alias_term(client, session, seeded):
    set_personal_aliases(session, by_zh(session, "土豆").id, ["马铃薯丁"], [])
    response = client.post("/api/ingredients", json={**USER_ENTRY, "name_zh": "马铃薯丁"})
    assert response.status_code == 409


def test_put_personal_aliases_endpoint(client, session, seeded):
    potato = by_zh(session, "土豆")
    body = client.put(
        f"/api/ingredients/{potato.id}/personal-aliases",
        json={"aliases_zh": [" 马铃薯丁 ", "马铃薯丁"], "aliases_en": []},
    ).json()
    assert body["user_aliases_zh"] == ["马铃薯丁"]


def test_put_personal_aliases_error_mapping(client, session, seeded, user_ingredient):
    potato = by_zh(session, "土豆")
    url = "/api/ingredients/{}/personal-aliases"
    body = {"aliases_zh": ["番茄"], "aliases_en": []}
    assert client.put(url.format(potato.id), json=body).status_code == 409
    assert client.put(url.format(user_ingredient.id), json=body).status_code == 422
    assert client.put(url.format(0), json=body).status_code == 404


def test_personal_aliases_can_be_cleared_and_are_searchable(client, session, seeded):
    potato = by_zh(session, "土豆")
    url = f"/api/ingredients/{potato.id}/personal-aliases"
    client.put(url, json={"aliases_zh": ["马铃薯丁"], "aliases_en": ["spud"]})
    found = client.get("/api/ingredients", params={"q": "spud"}).json()
    assert [row["id"] for row in found] == [potato.id]
    client.put(url, json={"aliases_zh": [], "aliases_en": []})
    assert client.get("/api/ingredients", params={"q": "spud"}).json() == []


def test_all_terms_includes_personal_aliases(session, seeded):
    potato = by_zh(session, "土豆")
    set_personal_aliases(session, potato.id, ["马铃薯丁"], ["spud"])
    terms = all_terms(potato)
    assert {"土豆", "马铃薯丁", "spud"} <= set(terms)
