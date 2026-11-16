import pytest
from sqlalchemy import select

from minemydinner.dictionary.service import (
    IngredientWrite,
    create_user_ingredient,
    link_unmatched_lines,
)
from minemydinner.extraction.allergen_suggest import (
    Suggestion,
    load_prompt,
    suggest_entry,
)
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


OLD_PROMPT = (
    "List which of these allergens the ingredient may contain: crustaceans, shellfish, fish, "
    "egg, milk, peanut, tree nut, soy, wheat, sesame. Use only these exact values. Include an "
    "allergen when a common version of the ingredient contains it. Return an empty list when "
    "none apply."
)


def test_v1_prompt_matches_the_old_text():
    assert load_prompt("allergen_suggest_v1") == OLD_PROMPT


def test_v2_prompt_lists_categories_and_stock_modes():
    from minemydinner.constants import CATEGORIES, STOCK_MODES

    prompt = load_prompt()
    assert all(value in prompt for value in (*CATEGORIES, *STOCK_MODES))


def test_suggest_entry_parses_all_fields():
    client = FakeLLMClient(
        payload={"allergens": ["egg"], "category": "egg and dairy", "stock_mode": "count"}
    )
    assert suggest_entry(client, "鸡蛋") == Suggestion(["egg"], "egg and dairy", "count")


def test_suggest_entry_drops_invalid_values():
    client = FakeLLMClient(
        payload={"allergens": ["shrimp", "soy", "soy"], "category": "dairy", "stock_mode": "grams"}
    )
    assert suggest_entry(client, "秘制酱") == Suggestion(["soy"], None, None)


def test_suggest_entry_is_none_when_no_model_is_available():
    assert suggest_entry(FakeLLMClient(error=AllModelsFailed("down")), "秘制酱") is None
    assert suggest_entry(FakeLLMClient(error=MonthlyCapExceeded("over")), "秘制酱") is None


def test_suggestion_route_returns_category_and_stock_mode(client):
    use_llm(
        FakeLLMClient(payload={"allergens": ["sesame"], "category": "seasoning", "stock_mode": "level"})
    )
    assert client.post("/api/allergen-suggestions", json={"name": "麻酱"}).json() == {
        "suggested": ["sesame"],
        "category": "seasoning",
        "stock_mode": "level",
    }
    use_llm(FakeLLMClient(error=AllModelsFailed("down")))
    assert client.post("/api/allergen-suggestions", json={"name": "麻酱"}).json() == {
        "suggested": None,
        "category": None,
        "stock_mode": None,
    }


class NamedClient:
    """Answers each name from a table, like the model would."""

    def __init__(self, answers):
        self.answers = answers

    def invoke_structured(self, task, messages, schema):
        from minemydinner.llm.client import StructuredResult

        name = messages[-1].content.removeprefix("Ingredient: ")
        return StructuredResult(schema.model_validate(self.answers[name]), "gemini", False)


def test_check_script_scores_fields(engine):
    from sqlalchemy.orm import sessionmaker

    from minemydinner.evaluation.allergen_suggest import run_check

    names = [
        {"name": "鸡蛋", "category": "egg and dairy", "stock_mode": "count", "allergens": ["egg"]},
        {"name": "牛奶", "category": "egg and dairy", "stock_mode": "level", "allergens": ["milk"]},
    ]
    client = NamedClient(
        {
            "鸡蛋": {"allergens": ["egg"], "category": "egg and dairy", "stock_mode": "count"},
            "牛奶": {"allergens": ["milk"], "category": "other", "stock_mode": "level"},
        }
    )
    report = run_check(sessionmaker(bind=engine), client, names)
    assert report.total == 2
    assert report.matches == {"allergens": 2, "category": 1, "stock_mode": 2}
    assert report.mismatches == ["牛奶: category got other, expected egg and dairy"]


def test_names_file_holds_valid_expected_values():
    from pathlib import Path

    from minemydinner.constants import ALLERGENS, CATEGORIES, STOCK_MODES
    from minemydinner.evaluation.allergen_suggest import load_names

    path = Path(__file__).resolve().parents[1] / "eval_data/allergen_suggest/names.yaml"
    names = load_names(path)
    assert len(names) == 18
    assert {item["stock_mode"] for item in names} == set(STOCK_MODES)
    assert len({item["category"] for item in names}) >= 8
    for item in names:
        assert item["category"] in CATEGORIES
        assert set(item["allergens"]) <= set(ALLERGENS)


def test_linked_lines_are_queried_by_exact_name(session, seeded, recipe_with_lines_named):
    recipe_with_lines_named(" 秘制酱 ")
    entry = create_user_ingredient(
        session, IngredientWrite(name_zh="秘制酱", category="other", stock_mode="level")
    )
    link_unmatched_lines(session, entry)
    query = select(RecipeIngredient).where(RecipeIngredient.ingredient_id == entry.id)
    assert len(session.scalars(query).all()) == 1
