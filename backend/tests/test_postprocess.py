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


def choice_line(name, choice_group):
    return ExtractedIngredient(
        raw=name, name=name, amount=None, unit=None, amount_type="missing",
        role="secondary", choice_group=choice_group,
    )


def test_singleton_group_dropped():
    lines = [choice_line("酸奶", 1), choice_line("吐司", None)]
    result = enforce_amount_rules(recipe_with(lines))
    assert [line.choice_group for line in result.ingredients] == [None, None]


def test_real_group_kept():
    lines = [choice_line("酸奶", 1), choice_line("巧克力榛果酱", 1)]
    result = enforce_amount_rules(recipe_with(lines))
    assert [line.choice_group for line in result.ingredients] == [1, 1]


def method_line(name, amount, unit):
    return ExtractedIngredient(
        raw=name, name=name, amount=amount, unit=unit, amount_type="spoon", role="seasoning"
    )


def test_method_amount_survives_when_source_states_it():
    source = "食材：生抽\n做法：放入生抽4勺腌制半小时"
    result = enforce_amount_rules(recipe_with([method_line("生抽", 4, "spoon")]), source)
    assert (result.ingredients[0].amount, result.ingredients[0].unit) == (4, "spoon")


def test_chinese_numeral_method_amount_survives():
    source = "食材：水\n做法：加水两勺调成糊"
    result = enforce_amount_rules(recipe_with([method_line("水", 2, "spoon")]), source)
    assert result.ingredients[0].amount == 2


def test_invented_amount_still_cleared_with_source():
    source = "食材：盐\n做法：按口味加盐调味即可"
    result = enforce_amount_rules(recipe_with([method_line("盐", 1, "teaspoon")]), source)
    assert result.ingredients[0].amount is None
    assert result.ingredients[0].amount_type == "missing"


def test_mixed_optional_group_made_uniform():
    lines = [
        choice_line("酸奶", 1),
        choice_line("巧克力榛果酱", 1).model_copy(update={"optional": True}),
    ]
    result = enforce_amount_rules(recipe_with(lines))
    assert [line.optional for line in result.ingredients] == [False, False]


def test_neighbour_amount_in_list_does_not_corroborate():
    source = "食材：鸡蛋2个、葱花、盐"
    result = enforce_amount_rules(recipe_with([method_line("盐", 5, "g")]), source)
    assert result.ingredients[0].amount is None
    assert result.ingredients[0].amount_type == "missing"


def test_step_number_does_not_corroborate():
    source = "食材：生抽\n做法：1.热锅 2.放入生抽炒匀"
    result = enforce_amount_rules(recipe_with([method_line("生抽", 3, "spoon")]), source)
    assert result.ingredients[0].amount is None
