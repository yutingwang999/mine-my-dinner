import pytest

from minemydinner.main import app
from minemydinner.models import ExtractionLog, Ingredient, Recipe
from minemydinner.settings import Settings, get_settings
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import count, line, one, saved


@pytest.fixture
def settings_max_logs_1(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, extraction_log_max_rows=1
    )


def test_save_writes_recipe_lines_and_cookware(client, session, seeded, fake_llm, recipe_write):
    response = client.post("/api/recipes", json=recipe_write)
    assert response.status_code == 201
    recipe = session.get(Recipe, response.json()["id"])
    assert recipe.source_url == recipe_write["source_url"] and recipe.embedding is not None
    assert {line.raw for line in recipe.ingredients} == {"少量葱花", "秘制酱"}
    assert [item.cookware_id for item in recipe.cookware] == recipe_write["cookware_ids"]
    assert (recipe.lang, recipe.time_source, recipe.tags) == ("zh", "stated", ["快手"])


def test_save_keeps_unmatched_line_with_empty_id(client, session, seeded, fake_llm, recipe_write):
    recipe = saved(client, session, recipe_write)
    assert any(line.ingredient_id is None for line in recipe.ingredients)


def test_save_writes_log_with_raw_model_output_and_correction(
    client, session, seeded, fake_llm, recipe_write_with_invented_amount
):
    client.post("/api/recipes", json=recipe_write_with_invented_amount)
    log = one(session, ExtractionLog)
    assert log.model_output["ingredients"][0]["amount"] == 5
    assert log.user_corrected["ingredients"][0]["amount"] is None
    assert log.user_corrected["lang"] == "zh" and "provenance" not in log.user_corrected
    assert (log.model_name, log.prompt_version) == ("gemini", "extract_v1")
    assert log.input_text == "葱花蛋：少量葱花，秘制酱"


def test_manual_save_writes_no_log(client, session, seeded, fake_llm, recipe_write):
    data = {**recipe_write, "provenance": None, "source_type": "manual"}
    assert client.post("/api/recipes", json=data).status_code == 201
    assert count(session, ExtractionLog) == 0


def test_log_stops_at_row_limit_but_recipe_saves(
    client, session, seeded, fake_llm, recipe_write, settings_max_logs_1
):
    client.post("/api/recipes", json=recipe_write)
    client.post("/api/recipes", json=recipe_write)
    assert count(session, Recipe) == 2 and count(session, ExtractionLog) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"title": " "},
        {"ingredients": []},
        {"dish_type": "main"},
        {"cookware_ids": [999999]},
        {"time_min": None, "time_source": "stated"},
        {"time_min": 0},
        {"servings": 0},
        {"lang": "fr"},
        {"source_type": "video"},
    ],
)
def test_invalid_recipes_are_rejected(client, session, seeded, fake_llm, recipe_write, mutation):
    assert client.post("/api/recipes", json={**recipe_write, **mutation}).status_code == 422
    assert count(session, Recipe) == 0


def test_unknown_ingredient_id_is_rejected(client, session, seeded, fake_llm, recipe_write):
    recipe_write["ingredients"][0]["ingredient_id"] = 999999
    assert client.post("/api/recipes", json=recipe_write).status_code == 422
    assert count(session, Recipe) == 0


def test_blank_line_name_is_rejected(client, session, seeded, fake_llm, recipe_write):
    recipe_write["ingredients"][0]["name"] = "  "
    assert client.post("/api/recipes", json=recipe_write).status_code == 422


def test_vague_line_cannot_carry_an_amount(client, session, seeded, fake_llm, recipe_write):
    recipe_write["ingredients"][0] |= {"amount": 5, "unit": "g", "amount_type": "vague"}
    assert client.post("/api/recipes", json=recipe_write).status_code == 422


def test_supplies_are_saved_cleaned(client, session, seeded, fake_llm, recipe_write):
    body = {**recipe_write, "supplies": [" 保鲜膜 ", "锡纸", "", "保鲜膜"]}
    recipe = saved(client, session, body)
    assert recipe.supplies == ["保鲜膜", "锡纸"]
    assert client.get(f"/api/recipes/{recipe.id}").json()["supplies"] == ["保鲜膜", "锡纸"]


@pytest.mark.parametrize("unit", ["tablespoon", "teaspoon", "spoon"])
def test_spoon_sizes_are_valid_units(client, session, seeded, fake_llm, recipe_write, unit):
    recipe_write["ingredients"][0] |= {"amount": 1, "unit": unit, "amount_type": "spoon"}
    assert saved(client, session, recipe_write).ingredients[0].unit == unit


def test_household_measure_may_keep_a_number_without_a_unit(
    client, session, seeded, fake_llm, recipe_write
):
    recipe_write["ingredients"][0] |= {"raw": "一碗水", "amount": 1, "unit": None, "amount_type": "spoon"}
    line = saved(client, session, recipe_write).ingredients[0]
    assert (line.amount, line.unit) == (1, None)


@pytest.mark.parametrize(
    ("amount", "unit", "amount_type"),
    [
        (4, "spoon", "count"),
        (1, "teaspoon", "exact"),
        (200, "g", "spoon"),
        (2, "piece", "exact"),
        (1, None, "count"),
        (0, "teaspoon", "spoon"),
    ],
)
def test_amount_type_must_match_the_unit(
    client, session, seeded, fake_llm, recipe_write, amount, unit, amount_type
):
    recipe_write["ingredients"][0] |= {"amount": amount, "unit": unit, "amount_type": amount_type}
    assert client.post("/api/recipes", json=recipe_write).status_code == 422
    assert count(session, Recipe) == 0


@pytest.mark.parametrize(
    ("amount", "unit", "amount_type"),
    [(200, "g", "exact"), (100, "ml", "exact"), (2, "stalk", "count"), (1, None, "spoon")],
)
def test_matching_amount_type_and_unit_are_saved(
    client, session, seeded, fake_llm, recipe_write, amount, unit, amount_type
):
    recipe_write["ingredients"][0] |= {"amount": amount, "unit": unit, "amount_type": amount_type}
    assert saved(client, session, recipe_write).ingredients[0].amount_type == amount_type


def test_user_time_source_is_kept(client, session, seeded, fake_llm, recipe_write):
    recipe = saved(client, session, {**recipe_write, "time_min": 25, "time_source": "user"})
    assert (recipe.time_min, recipe.time_source) == (25, "user")


def test_duplicate_cookware_is_saved_once(client, session, seeded, fake_llm, recipe_write):
    recipe_write["cookware_ids"] *= 2
    assert len(saved(client, session, recipe_write).cookware) == 1


def test_choice_group_saves_and_rereads(client, session, seeded, fake_llm, recipe_write):
    recipe_write["ingredients"][0]["choice_group"] = 1
    recipe_write["ingredients"][1]["choice_group"] = 1
    recipe = saved(client, session, recipe_write)
    assert [line.choice_group for line in recipe.ingredients] == [1, 1]
    lines = client.get(f"/api/recipes/{recipe.id}").json()["ingredients"]
    assert [line["choice_group"] for line in lines] == [1, 1]


def test_singleton_choice_group_rejected(client, session, seeded, fake_llm, recipe_write):
    recipe_write["ingredients"][0]["choice_group"] = 1
    assert client.post("/api/recipes", json=recipe_write).status_code == 422
    assert count(session, Recipe) == 0


def test_mixed_optional_in_group_rejected(client, session, seeded, fake_llm, recipe_write):
    recipe_write["ingredients"][0]["choice_group"] = 1
    recipe_write["ingredients"][1] |= {"choice_group": 1, "optional": True}
    assert client.post("/api/recipes", json=recipe_write).status_code == 422
    assert count(session, Recipe) == 0


def test_same_group_id_in_two_recipes_is_fine(client, session, seeded, fake_llm, recipe_write):
    recipe_write["ingredients"][0]["choice_group"] = 1
    recipe_write["ingredients"][1]["choice_group"] = 1
    assert client.post("/api/recipes", json=recipe_write).status_code == 201
    assert client.post("/api/recipes", json=recipe_write).status_code == 201


def remember_setup(session, recipe_write, **owner_fields):
    entry = Ingredient(
        name_zh="测试西兰花", name_en="Test Broccoli", allergens=[],
        category="vegetable", stock_mode="level", source="seed",
    )
    session.add(entry)
    session.flush()
    if owner_fields:
        session.add(
            Ingredient(
                name_zh="测试花菜", name_en="Test Cauliflower", allergens=[],
                category="vegetable", stock_mode="level", source="seed",
                **owner_fields,
            )
        )
        session.flush()
    recipe_write["ingredients"].append(
        line("西蓝花", "西蓝花", entry.id, amount_type="missing", remember_link=True)
    )
    return entry


def test_save_with_remember_link_adds_alias(client, session, seeded, fake_llm, recipe_write):
    entry = remember_setup(session, recipe_write)
    response = client.post("/api/recipes", json=recipe_write)
    assert response.status_code == 201 and response.json()["alias_conflicts"] == []
    session.expire_all()
    assert entry.user_aliases_zh == ["西蓝花"]
    recipe = session.get(Recipe, response.json()["id"])
    assert not hasattr(recipe.ingredients[0], "remember_link")
    log = one(session, ExtractionLog)
    assert all("remember_link" not in item for item in log.user_corrected["ingredients"])


def test_save_without_remember_link_changes_no_dictionary(
    client, session, seeded, fake_llm, recipe_write
):
    entry = remember_setup(session, recipe_write)
    recipe_write["ingredients"][-1]["remember_link"] = False
    assert client.post("/api/recipes", json=recipe_write).status_code == 201
    session.expire_all()
    assert entry.user_aliases_zh == []


def test_save_conflict_still_saves(client, session, seeded, fake_llm, recipe_write):
    entry = remember_setup(session, recipe_write, user_aliases_zh=["西蓝花"])
    response = client.post("/api/recipes", json=recipe_write)
    assert response.status_code == 201
    assert response.json()["alias_conflicts"] == ["西蓝花"]
    session.expire_all()
    recipe = session.get(Recipe, response.json()["id"])
    assert recipe.ingredients[-1].ingredient_id == entry.id
    assert entry.user_aliases_zh == []


def test_save_remembers_the_name_as_saved(client, session, seeded, fake_llm, recipe_write):
    entry = remember_setup(session, recipe_write)
    recipe_write["ingredients"][-1]["name"] = "西蓝花碎"
    assert client.post("/api/recipes", json=recipe_write).status_code == 201
    session.expire_all()
    assert entry.user_aliases_zh == ["西蓝花碎"]
