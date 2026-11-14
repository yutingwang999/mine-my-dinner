from minemydinner.planning.feasibility import REASONS, Shortfall, check_feasibility
from minemydinner.planning.validate import PlanSpec
from tests.planningfakes import (
    L,
    always_in_stock,
    ctx_for,
    make_recipe,
    not_always_in_stock,
    own_cookware,
    stock,
)
from tests.recipefakes import seeded  # noqa: F401


def spec(**kwargs) -> PlanSpec:
    return PlanSpec(**{"mode": "shopping", "dishes": 0, "soups": 0, **kwargs})


def check(session, plan_spec):
    return check_feasibility(session, ctx_for(session), plan_spec)


def reasons(found):
    return [s.reason for s in found]


def test_reasons_are_in_report_order():
    assert REASONS == (
        "empty_inventory",
        "no_cookware",
        "not_enough_dishes",
        "time_limit",
        "not_enough_in_library",
        "named_ingredient",
    )


def test_empty_fridge_in_fridge_only_mode(session, seeded):
    make_recipe(session, "炒蛋", lines=[L("鸡蛋", role="main")])
    found = check(session, spec(mode="fridge_only", dishes=1))
    assert found == [Shortfall("empty_inventory", None, None, None)]


def test_empty_fridge_is_fine_when_shopping(session, seeded):
    make_recipe(session, "炒蛋", lines=[L("鸡蛋", role="main")])
    assert check(session, spec(dishes=1)) == []


def test_not_enough_in_library_reports_counts(session, seeded):
    make_recipe(session, "炒青菜", dish_type="vegetable dish")
    found = check(session, spec(dishes=1, meat_dishes=1))
    assert Shortfall("not_enough_in_library", "meat_dishes", 1, 0) in found


def test_combined_dishes_are_counted_when_no_split_is_named(session, seeded):
    make_recipe(session, "炒青菜", dish_type="vegetable dish")
    make_recipe(session, "红烧肉", dish_type="meat dish")
    assert check(session, spec(dishes=2)) == []
    found = check(session, spec(dishes=3))
    assert found == [Shortfall("not_enough_in_library", "dishes", 3, 2)]


def test_not_enough_dishes_from_stock(session, seeded):
    make_recipe(session, "炒鸡", dish_type="meat dish", lines=[L("鸡胸肉", role="main")])
    stock(session, "鸡蛋", count=3)
    found = check(session, spec(mode="fridge_only", dishes=1, meat_dishes=1))
    assert Shortfall("not_enough_dishes", "meat_dishes", 1, 0) in found
    assert "not_enough_in_library" not in reasons(found)


def test_no_cookware_per_category(session, seeded):
    make_recipe(session, "烤肉", dish_type="meat dish", cookware=("烤箱",))
    make_recipe(session, "炒菜", dish_type="vegetable dish", cookware=("炒锅",))
    own_cookware(session, "炒锅")
    found = check(session, spec(dishes=2, meat_dishes=1, vegetable_dishes=1))
    assert found == [Shortfall("no_cookware", "meat_dishes", 1, 0)]


def test_time_limit_counts_only_firm_times(session, seeded):
    make_recipe(session, "慢炖", dish_type="meat dish", time_min=90, time_source="stated")
    make_recipe(session, "估计久", dish_type="vegetable dish", time_min=90, time_source="estimated")
    make_recipe(session, "没写", dish_type="soup")
    make_recipe(session, "久汤", dish_type="staple food", time_min=60, time_source="user")
    found = check(
        session, spec(dishes=2, meat_dishes=1, vegetable_dishes=1, soups=1, staples=1, time_limit_min=30)
    )
    # Only firm times over the limit rule a category out; an estimate or no time fits.
    assert {(s.reason, s.category) for s in found} == {
        ("time_limit", "staples"),
        ("time_limit", "meat_dishes"),
    }


def test_choice_group_and_child_stock_count_as_makeable(session, seeded):
    make_recipe(
        session,
        "鸡或猪",
        dish_type="meat dish",
        lines=[L("鸡胸肉", role="main", choice_group=1), L("五花肉", role="main", choice_group=1)],
    )
    make_recipe(session, "炖猪肉", dish_type="vegetable dish", lines=[L("猪肉", role="main")])
    stock(session, "五花肉", level="plenty")
    found = check(session, spec(mode="fridge_only", dishes=2, meat_dishes=1, vegetable_dishes=1))
    assert found == []


def test_always_in_stock_counts_as_stock(session, seeded):
    # 酱油 is always in stock after seeding; a dish needing only it is makeable.
    make_recipe(session, "拌酱油", lines=[L("酱油", role="seasoning")])
    stock(session, "鸡蛋", count=1)
    assert check(session, spec(mode="fridge_only", dishes=1)) == []
    not_always_in_stock(session, "酱油")
    found = check(session, spec(mode="fridge_only", dishes=1))
    assert Shortfall("not_enough_dishes", "dishes", 1, 0) in found


def test_minimum_split_compares_as_minimum(session, seeded):
    make_recipe(session, "红烧肉", dish_type="meat dish")
    make_recipe(session, "炒青菜", dish_type="vegetable dish")
    make_recipe(session, "炒青菜二", dish_type="vegetable dish")
    # At least one meat dish among three: two vegetable dishes may fill the rest.
    assert check(session, spec(dishes=3, meat_dishes=1, split_is_minimum=True)) == []
    found = check(session, spec(dishes=4, meat_dishes=1, split_is_minimum=True))
    assert found == [Shortfall("not_enough_in_library", "dishes", 4, 3)]


def test_satisfiable_request_returns_nothing(session, seeded):
    make_recipe(session, "炒肉", dish_type="meat dish", lines=[L("鸡胸肉", role="main")])
    make_recipe(session, "番茄汤", dish_type="soup", lines=[L("番茄", role="main")])
    stock(session, "鸡胸肉", count=2)
    stock(session, "番茄", count=2)
    assert check(session, spec(mode="fridge_only", dishes=1, soups=1, time_limit_min=30)) == []


def test_all_use_without_the_ingredient_in_stock(session, seeded):
    make_recipe(session, "炒蛋", lines=[L("鸡蛋", role="main")])
    stock(session, "番茄", count=2)
    found = check(session, spec(mode="fridge_only", dishes=1, all_use=["鸡蛋"]))
    assert Shortfall("named_ingredient", None, None, None, "鸡蛋", absent_from_stock=True) in found


def test_all_use_counts_per_category(session, seeded):
    for title in ("蛋一", "蛋二", "蛋三"):
        make_recipe(session, title, lines=[L("鸡蛋", role="main"), L("番茄", role="main")])
    # Two vegetable dishes need 青椒 that is not in stock; one is made from stock only.
    make_recipe(session, "蛋四", lines=[L("鸡蛋", role="main")])
    stock(session, "鸡蛋", count=6)
    found = check(session, spec(mode="fridge_only", dishes=3, vegetable_dishes=3, all_use=["鸡蛋"]))
    assert Shortfall("not_enough_dishes", "vegetable_dishes", 3, 1) in found
    assert Shortfall("named_ingredient", "vegetable_dishes", 3, 1, "鸡蛋") in found


def test_must_use_used_by_some_dish_passes(session, seeded):
    make_recipe(session, "炒蛋", lines=[L("鸡蛋", role="main")])
    make_recipe(session, "炒菜", lines=[L("番茄", role="main")])
    assert check(session, spec(dishes=1, must_use=["鸡蛋"])) == []
    found = check(session, spec(dishes=1, must_use=["牛肉"]))
    assert found == [Shortfall("named_ingredient", None, None, None, "牛肉")]


def test_unknown_must_use_name_passes_the_stock_check(session, seeded):
    make_recipe(session, "炒蛋", lines=[L("鸡蛋", role="main"), L("神秘香料", linked=False, optional=True)])
    stock(session, "鸡蛋", count=2)
    assert check(session, spec(mode="fridge_only", dishes=1, must_use=["神秘香料"])) == []


def test_empty_fridge_still_reports_cookware(session, seeded):
    make_recipe(
        session, "烤肉", dish_type="meat dish", lines=[L("鸡胸肉", role="main")], cookware=("烤箱",)
    )
    found = check(session, spec(mode="fridge_only", dishes=1, meat_dishes=1))
    assert reasons(found) == ["empty_inventory", "no_cookware"]
    assert "not_enough_dishes" not in reasons(found)


def test_empty_fridge_reports_library_shortfall_too(session, seeded):
    found = check(session, spec(mode="fridge_only", dishes=1, meat_dishes=1))
    assert reasons(found) == ["empty_inventory", "not_enough_in_library"]


def test_short_library_still_reports_cookware(session, seeded):
    make_recipe(session, "烤肉", dish_type="meat dish", cookware=("烤箱",))
    found = check(session, spec(dishes=2, meat_dishes=2))
    assert Shortfall("not_enough_in_library", "meat_dishes", 2, 1) in found
    assert Shortfall("no_cookware", "meat_dishes", 2, 0) in found


def test_short_library_still_reports_time_limit(session, seeded):
    make_recipe(session, "慢炖", dish_type="meat dish", time_min=90, time_source="stated")
    found = check(session, spec(dishes=2, meat_dishes=2, time_limit_min=30))
    assert Shortfall("not_enough_in_library", "meat_dishes", 2, 1) in found
    assert Shortfall("time_limit", "meat_dishes", 2, 0) in found


def test_combined_named_dishes_shortfall_keeps_other_reasons(session, seeded):
    make_recipe(session, "蛋肉", dish_type="meat dish", lines=[L("鸡蛋", role="main")])
    make_recipe(session, "素一", dish_type="vegetable dish", lines=[L("番茄", role="main")])
    make_recipe(session, "素二", dish_type="vegetable dish", lines=[L("青椒", role="main")])
    stock(session, "鸡蛋", count=3)
    stock(session, "番茄", count=3)
    found = check(
        session,
        spec(mode="fridge_only", dishes=3, meat_dishes=1, vegetable_dishes=1, all_use=["鸡蛋"]),
    )
    assert Shortfall("named_ingredient", "vegetable_dishes", 1, 0, "鸡蛋") in found
    assert Shortfall("not_enough_dishes", "dishes", 3, 2) in found
    assert not any(s.reason == "named_ingredient" and s.category == "dishes" for s in found)


def test_named_check_does_not_fire_on_an_empty_pool(session, seeded):
    found = check(session, spec(staples=1, must_use=["鸡蛋"], all_use=["鸡蛋"]))
    assert found == [Shortfall("not_enough_in_library", "staples", 1, 0)]


def test_seasoning_with_zero_count_is_available(session, seeded):
    not_always_in_stock(session, "酱油")
    make_recipe(session, "拌酱油", lines=[L("酱油", role="seasoning")])
    stock(session, "酱油", count=0)
    assert check(session, spec(mode="fridge_only", dishes=1, must_use=["酱油"])) == []


def test_optional_only_recipe_is_makeable(session, seeded):
    make_recipe(session, "随意", lines=[L("鸡蛋", role="main", optional=True)])
    stock(session, "番茄", count=1)
    assert check(session, spec(mode="fridge_only", dishes=1)) == []


def test_no_cookware_with_only_one_side_named(session, seeded):
    make_recipe(session, "烤菜", dish_type="vegetable dish", cookware=("烤箱",))
    make_recipe(session, "炒肉", dish_type="meat dish")
    found = check(session, spec(dishes=2, vegetable_dishes=1))
    assert found == [Shortfall("no_cookware", "vegetable_dishes", 1, 0)]


def test_empty_fridge_but_always_in_stock_item_makes_the_dish(session, seeded):
    always_in_stock(session, "鸡蛋")
    make_recipe(session, "蒸蛋", lines=[L("鸡蛋", role="main")])
    assert check(session, spec(mode="fridge_only", dishes=1)) == []


def test_empty_fridge_with_only_seasonings_and_nothing_makeable(session, seeded):
    make_recipe(session, "炒肉", dish_type="meat dish", lines=[L("鸡胸肉", role="main")])
    found = check(session, spec(mode="fridge_only", dishes=1))
    assert reasons(found) == ["empty_inventory"]


def test_empty_fridge_one_makeable_but_two_needed(session, seeded):
    always_in_stock(session, "鸡蛋")
    make_recipe(session, "蒸蛋", lines=[L("鸡蛋", role="main")])
    make_recipe(session, "炒肉", dish_type="meat dish", lines=[L("鸡胸肉", role="main")])
    found = check(session, spec(mode="fridge_only", dishes=2))
    assert found == [Shortfall("not_enough_dishes", "dishes", 2, 1)]


def test_split_minimum_marks_side_shortfalls_as_minimum(session, seeded):
    make_recipe(session, "炒青菜", dish_type="vegetable dish")
    found = check(session, spec(dishes=2, meat_dishes=1, split_is_minimum=True))
    meat = [s for s in found if s.category == "meat_dishes"]
    assert meat and all(s.minimum for s in meat)
    exact = check(session, spec(dishes=1, meat_dishes=1))
    assert not any(s.minimum for s in exact)


def test_stocked_but_unmakeable_named_ingredient_is_not_absent(session, seeded):
    make_recipe(session, "炒蛋", lines=[L("鸡蛋", role="main"), L("番茄", role="main")])
    make_recipe(session, "炒菜", lines=[L("番茄", role="main")])
    stock(session, "鸡蛋", count=2)
    stock(session, "番茄", count=1)
    found = check(session, spec(mode="fridge_only", dishes=1, must_use=["鸡蛋"]))
    assert not any(s.absent_from_stock for s in found)
