from minemydinner.planning.composition import (
    composition_gap,
    count_categories,
    required_counts,
)
from minemydinner.planning.named import recipes_using
from minemydinner.planning.validate import PlanSpec
from tests.planningfakes import L, ctx_for, make_recipe
from tests.recipefakes import seeded  # noqa: F401


def test_count_categories_ignores_other():
    counts = count_categories(
        ["meat dish", "vegetable dish", "vegetable dish", "soup", "staple food", "other"]
    )
    assert counts == {
        "dishes": 3,
        "soups": 1,
        "meat_dishes": 1,
        "vegetable_dishes": 2,
        "staples": 1,
        "desserts": 0,
        "snacks": 0,
    }


def test_required_counts_names_split_only_when_given():
    plain = PlanSpec(mode="shopping", dishes=2, soups=1)
    assert required_counts(plain) == {
        "dishes": 2,
        "soups": 1,
        "staples": 0,
        "desserts": 0,
        "snacks": 0,
    }
    split = PlanSpec(mode="shopping", dishes=2, soups=0, meat_dishes=1)
    required = required_counts(split)
    assert required["meat_dishes"] == 1 and "vegetable_dishes" not in required


def test_composition_gap_prefers_two_meat_over_one():
    spec = PlanSpec(mode="shopping", dishes=6, soups=0, meat_dishes=3, vegetable_dishes=3)
    two = {"dishes": 5, "meat_dishes": 2, "vegetable_dishes": 3}
    one = {"dishes": 4, "meat_dishes": 1, "vegetable_dishes": 3}
    assert composition_gap(spec, two) < composition_gap(spec, one)


def test_composition_gap_minimum_counts_only_shortfall():
    spec = PlanSpec(
        mode="shopping",
        dishes=3,
        soups=0,
        meat_dishes=1,
        vegetable_dishes=1,
        split_is_minimum=True,
    )
    assert composition_gap(spec, {"dishes": 3, "meat_dishes": 1, "vegetable_dishes": 2}) == 0
    assert composition_gap(spec, {"dishes": 3, "meat_dishes": 0, "vegetable_dishes": 3}) == 1


def test_recipes_using_matches_search_rule(session, seeded):
    belly = make_recipe(session, "红烧肉", lines=[L("五花肉")])
    plain = make_recipe(session, "白菜", lines=[L("小白菜")])
    unlinked = make_recipe(session, "神秘", lines=[L("某种酱", linked=False)])
    ctx = ctx_for(session)
    ids = [belly.id, plain.id, unlinked.id]
    assert recipes_using(session, ctx, "猪肉", ids) == {belly.id}
    assert recipes_using(session, ctx, "某种酱", ids) == {unlinked.id}
    assert recipes_using(session, ctx, "猪肉", []) == set()
