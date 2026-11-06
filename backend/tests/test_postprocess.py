import pytest

from minemydinner.extraction.postprocess import enforce_amount_rules
from minemydinner.extraction.schemas import ExtractedIngredient, ExtractedRecipe


def recipe_with(ingredients=(), **fields) -> ExtractedRecipe:
    values = {
        "lang": "zh",
        "title": "测试菜",
        "dish_type": "vegetable dish",
        "time_min": 10,
        "time_source": "stated",
        "servings": 2,
        "tags": [],
        "steps_summary": None,
        "ingredients": list(ingredients),
        "cookware": [],
    }
    return ExtractedRecipe(**(values | fields))


def enforce(raw, name, amount, unit, amount_type) -> ExtractedIngredient:
    line = ExtractedIngredient(
        raw=raw, name=name, amount=amount, unit=unit, amount_type=amount_type, role="main"
    )
    return enforce_amount_rules(recipe_with([line])).ingredients[0]


@pytest.mark.parametrize(
    ("raw", "name", "expected_type"),
    [
        ("少量葱花", "葱花", "vague"),
        ("芹菜", "芹菜", "missing"),
        ("a little salt", "salt", "vague"),
        ("Salt ", "salt", "missing"),
        ("一点盐", "盐", "vague"),
        ("一些香菜", "香菜", "vague"),
        ("a pinch of pepper", "pepper", "vague"),
    ],
)
def test_invented_amount_is_removed(raw, name, expected_type):
    line = enforce(raw=raw, name=name, amount=5, unit="g", amount_type="exact")
    assert (line.amount, line.unit, line.amount_type) == (None, None, expected_type)


@pytest.mark.parametrize(
    ("raw", "amount"),
    [
        ("2个土豆", 2),
        ("两个土豆", 2),
        ("半斤五花肉", 0.5),
        ("1/2 cup milk", 0.5),
        ("２个鸡蛋", 2),
        ("½ cup milk", 0.5),
        ("two eggs", 2),
        ("Half an onion", 0.5),
        ("a dozen shrimp", 12),
        ("一个鸡蛋", 1),
    ],
)
def test_amount_stated_in_the_source_is_kept(raw, amount):
    line = enforce(raw=raw, name="x", amount=amount, unit="piece", amount_type="count")
    assert (line.amount, line.unit, line.amount_type) == (amount, "piece", "count")


@pytest.mark.parametrize("amount_type", ["vague", "missing"])
def test_vague_and_missing_never_keep_a_number(amount_type):
    line = enforce(raw="2个少量", name="x", amount=2, unit="piece", amount_type=amount_type)
    assert (line.amount, line.unit, line.amount_type) == (None, None, amount_type)


def test_stated_time_without_time_min_is_cleared():
    assert enforce_amount_rules(recipe_with(time_min=None, time_source="stated")).time_source is None


def test_time_with_a_source_is_kept():
    recipe = enforce_amount_rules(recipe_with(time_min=20, time_source="estimated"))
    assert (recipe.time_min, recipe.time_source) == (20, "estimated")


def test_input_recipe_is_not_changed():
    line = ExtractedIngredient(
        raw="少量葱花", name="葱花", amount=5, unit="g", amount_type="exact", role="seasoning"
    )
    original = recipe_with([line])
    enforce_amount_rules(original)
    assert original.ingredients[0].amount == 5
