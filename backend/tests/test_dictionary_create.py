import pytest
from sqlalchemy import select

from minemydinner.dictionary.service import (
    IngredientWrite,
    create_user_ingredient,
    link_unmatched_lines,
)
from minemydinner.extraction.allergen_suggest import suggest_allergens
from minemydinner.llm.client import AllModelsFailed, MonthlyCapExceeded
from minemydinner.models import CookwareOwned, Ingredient, Recipe, RecipeIngredient
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import FakeLLMClient, by_zh, use_llm

INGREDIENT = {"category": "seasoning", "stock_mode": "level"}


def post_ingredient(client, **fields):
    return client.post("/api/ingredients", json=INGREDIENT | fields)


def create_ingredient(client, **fields) -> dict:
    response = post_ingredient(client, **fields)
    assert response.status_code == 201, response.text
    return response.json()


def post_cookware(client, **fields):
    return client.post("/api/cookware", json=fields)


def id_of(session, name: str) -> int:
    return by_zh(session, name).id


@pytest.fixture
def recipe_with_lines_named(session):
    def make(*names: str) -> Recipe:
        recipe = Recipe(
            lang="zh",
            title="测试菜",
            dish_type="other",
            source_type="manual",
            ingredients=[
                RecipeIngredient(raw=name, name=name, amount_type="missing", role="seasoning")
                for name in names
            ],
        )
        session.add(recipe)
        session.flush()
        return recipe

    return make


def test_new_entry_defaults_to_unknown_and_user_source(client, session, seeded):
    entry = create_ingredient(client, name_zh="秘制酱")
    assert entry["allergens"] is None and entry["source"] == "user"


def test_explicit_empty_means_confirmed_none(client, seeded):
    assert create_ingredient(client, name_zh="秘制酱", allergens=[])["allergens"] == []


def test_unlisted_allergen_is_rejected(client, seeded):
    assert post_ingredient(client, name_zh="秘制酱", allergens=["shrimp"]).status_code == 422


def test_entry_needs_a_name(client, seeded):
    assert post_ingredient(client, name_zh=" ", name_en=None).status_code == 422


@pytest.mark.parametrize("term", ["葱花", "生抽", "SOY SAUCE"])
def test_name_or_alias_collision_returns_409(client, seeded, term):
    response = post_ingredient(client, name_zh="新词", aliases_en=[term])
    assert response.status_code == 409
    assert response.json()["owner_id"] and response.json()["term"] == term


def test_name_collision_returns_409(client, seeded):
    assert post_ingredient(client, name_zh=" 葱 ").status_code == 409


def test_parent_must_be_top_level(client, seeded):
    assert post_ingredient(client, name_zh="新词", parent_id=id_of(seeded, "生抽")).status_code == 422
    assert post_ingredient(client, name_zh="新词", parent_id=999999).status_code == 422


def test_top_level_parent_is_accepted(client, seeded):
    entry = create_ingredient(client, name_zh="新酱油", parent_id=id_of(seeded, "酱油"))
    assert entry["parent_id"] == id_of(seeded, "酱油")


def test_new_entry_links_exact_name_rows_only(session, seeded, recipe_with_lines_named):
    recipe_with_lines_named("秘制酱", "秘制酱汁")
    entry = create_user_ingredient(
        session, IngredientWrite(name_zh="秘制酱", category="other", stock_mode="level")
    )
    assert link_unmatched_lines(session, entry) == 1


def test_create_route_links_matching_lines(client, session, seeded, recipe_with_lines_named):
    recipe = recipe_with_lines_named("秘制酱")
    entry = create_ingredient(client, name_zh="秘制酱")
    assert entry["linked_lines"] == 1
    session.refresh(recipe.ingredients[0])
    assert recipe.ingredients[0].ingredient_id == entry["id"]


def search_names(client, q) -> list[str]:
    return [item["name_zh"] for item in client.get("/api/ingredients", params={"q": q}).json()]


def test_search_ranks_an_exact_alias_before_partial_matches(client, seeded):
    # 食用油 has the alias "油"; 油麦菜 and others only contain it.
    assert search_names(client, "油")[0] == "食用油"
    assert search_names(client, " OIL ")[0] == "食用油"


def test_search_keeps_the_exact_name_when_partial_matches_pass_the_limit(client, session, seeded):
    for index in range(25):
        entry = Ingredient(
            name_zh=f"甲料{index:02d}", category="other", stock_mode="level", source="user"
        )
        session.add(entry)
    # Created last, so it has the highest id.
    session.add(Ingredient(name_zh="甲料", category="other", stock_mode="level", source="user"))
    session.flush()
    names = search_names(client, "甲料")
    assert len(names) == 20 and names[0] == "甲料"


def test_browse_returns_every_entry_when_asked(client, seeded):
    everything = client.get("/api/ingredients", params={"limit": 500}).json()
    assert len(everything) > 200
    assert {"seed"} <= {item["source"] for item in everything}
    assert len(client.get("/api/ingredients").json()) == 20


def test_browse_filters_by_category(client, seeded):
    seasonings = client.get("/api/ingredients", params={"limit": 500, "category": "seasoning"}).json()
    assert seasonings and {item["category"] for item in seasonings} == {"seasoning"}
    assert client.get("/api/ingredients", params={"category": "rocks"}).status_code == 422


def test_limit_is_bounded(client, seeded):
    assert client.get("/api/ingredients", params={"limit": 501}).status_code == 422
    assert client.get("/api/cookware", params={"limit": 0}).status_code == 422


def test_browse_cookware(client, seeded):
    assert len(client.get("/api/cookware", params={"limit": 500}).json()) >= 13


def test_search_ingredients_by_name_or_alias(client, seeded):
    names = [item["name_zh"] for item in client.get("/api/ingredients?q=葱花").json()]
    assert "葱" in names


def test_new_cookware_with_owned_adds_to_owned(client, session, seeded):
    response = post_cookware(client, name_zh="面包机", owned=True)
    assert response.status_code == 201
    assert session.get(CookwareOwned, response.json()["id"])


def test_new_cookware_not_owned_is_only_a_dictionary_entry(client, session, seeded):
    created = post_cookware(client, name_zh="面包机", owned=False).json()
    assert session.get(CookwareOwned, created["id"]) is None


def test_cookware_collision_returns_409(client, seeded):
    assert post_cookware(client, name_zh="新锅", aliases_en=["WOK"], owned=False).status_code == 409


def test_search_cookware(client, seeded):
    assert "炒锅" in [item["name_zh"] for item in client.get("/api/cookware?q=wok").json()]


def test_suggestions_drop_unlisted_and_survive_outage():
    suggesting = FakeLLMClient(payload={"allergens": ["shrimp", "soy", "soy"]})
    assert suggest_allergens(suggesting, "秘制酱") == ["soy"]
    assert suggest_allergens(FakeLLMClient(error=AllModelsFailed("down")), "秘制酱") is None
    assert suggest_allergens(FakeLLMClient(error=MonthlyCapExceeded("over")), "秘制酱") is None


def test_suggestion_route(client):
    use_llm(FakeLLMClient(payload={"allergens": ["sesame"]}))
    assert client.post("/api/allergen-suggestions", json={"name": "麻酱"}).json() == {
        "suggested": ["sesame"]
    }
    use_llm(FakeLLMClient(error=AllModelsFailed("down")))
    assert client.post("/api/allergen-suggestions", json={"name": "麻酱"}).json() == {
        "suggested": None
    }


def test_linked_lines_are_queried_by_exact_name(session, seeded, recipe_with_lines_named):
    recipe_with_lines_named(" 秘制酱 ")
    entry = create_user_ingredient(
        session, IngredientWrite(name_zh="秘制酱", category="other", stock_mode="level")
    )
    link_unmatched_lines(session, entry)
    query = select(RecipeIngredient).where(RecipeIngredient.ingredient_id == entry.id)
    assert len(session.scalars(query).all()) == 1
