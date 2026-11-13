import pytest
from sqlalchemy import select

from minemydinner.models import MealPlan, MealPlanItem, Rating, Recipe, RecipeIngredient
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import by_zh, count, line

LINE = line("两个鸡蛋", "鸡蛋", None, amount=2, unit="piece", amount_type="count", role="main")


def make_recipe(session, title, tags=(), names=(), dish_type="vegetable dish", embedded=True):
    recipe = Recipe(
        lang="zh",
        title=title,
        dish_type=dish_type,
        tags=list(tags),
        source_type="manual",
        embedding=[0.1] * 768 if embedded else None,
        ingredients=[
            RecipeIngredient(raw=name, name=name, amount_type="missing", role="main")
            for name in names
        ],
    )
    session.add(recipe)
    session.flush()
    return recipe


@pytest.fixture
def library(seeded):
    return [
        make_recipe(seeded, "番茄炒蛋", names=["番茄", "鸡蛋"]),
        make_recipe(seeded, "拍黄瓜", tags=["凉菜"], names=["黄瓜"]),
        make_recipe(seeded, "红烧肉", names=["五花肉"], dish_type="meat dish"),
    ]


def titles(client, **params) -> list[str]:
    response = client.get("/api/recipes", params=params)
    assert response.status_code == 200
    return [item["title"] for item in response.json()["items"]]


def recipe_body(recipe: Recipe, **fields) -> dict:
    return {
        "lang": recipe.lang,
        "title": recipe.title,
        "dish_type": recipe.dish_type,
        "time_min": None,
        "time_source": None,
        "servings": None,
        "tags": recipe.tags,
        "steps_summary": None,
        "ingredients": [LINE],
        "cookware_ids": [],
        "source_type": recipe.source_type,
        "source_url": None,
        "provenance": None,
    } | fields


def put_recipe(client, recipe, **fields) -> dict:
    response = client.put(f"/api/recipes/{recipe.id}", json=recipe_body(recipe, **fields))
    assert response.status_code == 200, response.text
    return response.json()


def test_search_matches_title_tag_and_ingredient_without_model_call(client, library, fake_llm):
    assert titles(client, q="番茄") == ["番茄炒蛋"]
    assert titles(client, q="凉菜") == ["拍黄瓜"]
    assert titles(client, q="鸡蛋") == ["番茄炒蛋"]
    assert fake_llm.call_count == 0 and fake_llm.embed_count == 0


def test_filter_by_dish_type_and_list_all(client, library):
    assert titles(client, dish_type="meat dish") == ["红烧肉"]
    assert sorted(titles(client)) == sorted(["番茄炒蛋", "拍黄瓜", "红烧肉"])


def test_search_treats_percent_literally(client, library):
    assert titles(client, q="%") == []


def test_pages(client, seeded):
    for index in range(25):
        make_recipe(seeded, f"菜{index:02d}")
    first = client.get("/api/recipes", params={"page": 1}).json()
    second = client.get("/api/recipes", params={"page": 2}).json()
    assert (len(first["items"]), len(second["items"]), first["total"]) == (20, 5, 25)


def test_saved_lines_carry_the_linked_entry_names(client, library, fake_llm, seeded):
    linked = LINE | {"name": "鸡蛋液", "ingredient_id": by_zh(seeded, "鸡蛋").id}
    updated = put_recipe(client, library[0], ingredients=[linked, LINE])
    assert updated["ingredients"][0]["linked_name_zh"] == "鸡蛋"
    assert updated["ingredients"][0]["linked_name_en"] == "egg"
    assert updated["ingredients"][1]["linked_name_zh"] is None


def test_get_returns_lines_and_cookware(client, library):
    body = client.get(f"/api/recipes/{library[0].id}").json()
    assert [item["name"] for item in body["ingredients"]] == ["番茄", "鸡蛋"]
    assert body["cookware_ids"] == [] and body["embedded"] is True


def test_get_names_cookware_in_the_recipe_language(client, seeded, library):
    from tests.recipefakes import cookware_id
    from minemydinner.models import RecipeCookware

    library[0].cookware = [RecipeCookware(cookware_id=cookware_id(seeded, "炒锅"))]
    seeded.flush()
    body = client.get(f"/api/recipes/{library[0].id}").json()
    assert body["cookware"] == [{"name": "炒锅", "cookware_id": cookware_id(seeded, "炒锅")}]


def test_missing_recipe_is_404(client, seeded):
    assert client.get("/api/recipes/999999").status_code == 404
    assert client.delete("/api/recipes/999999").status_code == 404


def test_edit_replaces_lines_and_recomputes_embedding(client, session, library, fake_llm):
    updated = put_recipe(client, library[0], title="新名字")
    assert updated["title"] == "新名字" and len(updated["ingredients"]) == 1
    assert fake_llm.embed_count == 1 and updated["embedded"] is True


def test_edit_writes_no_extraction_log(client, session, library, fake_llm):
    from minemydinner.models import ExtractionLog

    provenance = {"input_text": "x", "model_output": {}, "model_name": "m", "prompt_version": "v"}
    put_recipe(client, library[0], provenance=provenance)
    assert count(session, ExtractionLog) == 0


def test_failed_reembedding_keeps_edit_and_marks_pending(client, session, library, llm_embed_down):
    recipe = session.get(Recipe, put_recipe(client, library[0], title="新名字")["id"])
    assert recipe.title == "新名字" and recipe.embedding is None


def test_edit_rejects_invalid_body(client, library, fake_llm):
    body = recipe_body(library[0], ingredients=[])
    assert client.put(f"/api/recipes/{library[0].id}", json=body).status_code == 422


@pytest.fixture
def recipe_with_rating_plan_variant(session, seeded):
    recipe = make_recipe(session, "番茄炒蛋", names=["番茄"])
    variant = make_recipe(session, "番茄炒蛋（无蛋）", names=["番茄"])
    variant.parent_recipe_id = recipe.id
    session.add(Rating(recipe_id=recipe.id, score=5))
    plan = MealPlan(thread_id="t1", constraints={})
    session.add(plan)
    session.flush()
    item = MealPlanItem(plan_id=plan.id, recipe_id=recipe.id, recipe_title=recipe.title)
    session.add(item)
    session.flush()
    return recipe.id, item, variant


def test_delete_removes_ratings_and_keeps_plan_title_and_variant(
    client, session, recipe_with_rating_plan_variant
):
    recipe_id, item, variant = recipe_with_rating_plan_variant
    assert client.delete(f"/api/recipes/{recipe_id}").status_code == 204
    session.expire_all()
    assert count(session, Rating) == 0
    assert item.recipe_id is None and item.recipe_title == "番茄炒蛋"
    assert variant.parent_recipe_id is None
    assert session.scalars(select(Recipe.id).where(Recipe.id == variant.id)).one()


def test_edit_can_link_a_dictionary_entry(client, library, fake_llm, seeded):
    linked = LINE | {"ingredient_id": by_zh(seeded, "鸡蛋").id}
    updated = put_recipe(client, library[0], ingredients=[linked])
    assert updated["ingredients"][0]["ingredient_id"] == by_zh(seeded, "鸡蛋").id


def test_edit_rejects_singleton_choice_group(client, library, fake_llm):
    body = recipe_body(library[0], ingredients=[LINE | {"choice_group": 1}])
    assert client.put(f"/api/recipes/{library[0].id}", json=body).status_code == 422


def test_edit_rejects_mixed_optional_choice_group(client, library, fake_llm):
    lines = [
        LINE | {"choice_group": 1},
        LINE | {"raw": "备选", "name": "备选", "choice_group": 1, "optional": True},
    ]
    body = recipe_body(library[0], ingredients=lines)
    assert client.put(f"/api/recipes/{library[0].id}", json=body).status_code == 422


def test_edit_keeps_choice_groups(client, session, library, fake_llm):
    lines = [
        LINE | {"choice_group": 1},
        LINE | {"raw": "备选", "name": "备选", "choice_group": 1},
    ]
    updated = put_recipe(client, library[0], ingredients=lines)
    assert [line["choice_group"] for line in updated["ingredients"]] == [1, 1]


def _count_list_queries(client, session, count_queries, recipes: int) -> int:
    from minemydinner.models import RecipeImage
    from tests.planningfakes import L, make_recipe as make_linked

    for i in range(recipes):
        recipe = make_linked(session, f"菜{i}", lines=[L("葱"), L("土豆")], cookware=["炒锅"])
        recipe.images.append(
            RecipeImage(file_name=f"{i}.png", mime_type="image/png", size_bytes=1, position=0)
        )
    session.flush()
    session.expire_all()
    with count_queries() as counter:
        assert client.get("/api/recipes").status_code == 200
    return counter.count


def test_list_recipes_query_count_is_constant(client, session, seeded, count_queries):
    few = _count_list_queries(client, session, count_queries, 2)
    for recipe in session.scalars(select(Recipe)).all():
        session.delete(recipe)
    session.flush()
    many = _count_list_queries(client, session, count_queries, 5)
    assert few == many <= 8
