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


def counted_line(raw, name, amount):
    return ExtractedIngredient(
        raw=raw, name=name, amount=amount, unit="piece", amount_type="count", role="main"
    )


def test_english_word_amount_in_method_survives():
    line = counted_line("eggs", "eggs", 2)
    out = enforce_amount_rules(recipe_with([line]), "Whisk two eggs with the milk.")
    assert out.ingredients[0].amount == 2


def test_english_half_and_twelve_in_method_survive():
    source = "Slice half an onion. Add twelve shrimp."
    onion = enforce_amount_rules(recipe_with([counted_line("onion", "onion", 0.5)]), source)
    shrimp = enforce_amount_rules(recipe_with([counted_line("shrimp", "shrimp", 12)]), source)
    assert onion.ingredients[0].amount == 0.5 and shrimp.ingredients[0].amount == 12


def test_raw_not_in_source_does_not_vouch_for_amount():
    line = ExtractedIngredient(
        raw="盐 5g", name="盐", amount=5, unit="g", amount_type="exact", role="seasoning"
    )
    out = enforce_amount_rules(recipe_with([line]), "盐适量，翻炒均匀。")
    assert out.ingredients[0].amount is None and out.ingredients[0].amount_type == "vague"


def test_raw_in_source_still_vouches():
    line = ExtractedIngredient(
        raw="盐 5g", name="盐", amount=5, unit="g", amount_type="exact", role="seasoning"
    )
    out = enforce_amount_rules(recipe_with([line]), "食材：盐  5G\n做法：翻炒均匀。")
    assert out.ingredients[0].amount == 5


def test_screenshot_import_keeps_strict_guard():
    line = ExtractedIngredient(
        raw="盐 5g", name="盐", amount=5, unit="g", amount_type="exact", role="seasoning"
    )
    out = enforce_amount_rules(recipe_with([line]), None)
    assert out.ingredients[0].amount == 5


@pytest.mark.parametrize(
    ("source", "name", "amount"),
    [
        ("Drizzle honey over salt", "salt", 1),
        ("Cook often, add salt", "salt", 10),
        ("Add the weight of salt", "salt", 8),
        ("half a dozen eggs", "eggs", 12),
        ("Check the network for salt", "salt", 2),
        ("Add salt for the sixth time", "salt", 6),
    ],
)
def test_english_number_inside_another_word_does_not_corroborate(source, name, amount):
    out = enforce_amount_rules(recipe_with([counted_line(name, name, amount)]), source)
    assert out.ingredients[0].amount is None


def test_english_word_cut_by_the_window_edge_does_not_corroborate():
    # "one" ends exactly at the window edge but is part of "alone"; "two" is whole.
    source = "alone    salt"
    out = enforce_amount_rules(recipe_with([counted_line("salt", "salt", 1)]), source)
    assert out.ingredients[0].amount is None


@pytest.mark.parametrize(
    ("source", "name", "amount", "kept"),
    [
        ("Add salt 1.5g", "salt", 1.5, True),
        ("Add 1.5g salt", "salt", 1.5, True),
        ("加盐0.5勺", "盐", 0.5, True),
        ("Add salt 135", "salt", 1.5, False),
    ],
)
def test_decimal_amounts_match_as_plain_text(source, name, amount, kept):
    out = enforce_amount_rules(recipe_with([counted_line(name, name, amount)]), source)
    assert (out.ingredients[0].amount == amount) is kept
