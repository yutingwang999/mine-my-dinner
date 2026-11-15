import pytest

from minemydinner.planning.composition import (
    composition_gap,
    count_categories,
    required_counts,
    side_ranges,
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


def test_required_counts_leaves_the_sides_to_side_ranges():
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
    assert "meat_dishes" not in required and "vegetable_dishes" not in required


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
        meat_bound="at_least",
        vegetable_bound="at_least",
    )
    assert composition_gap(spec, {"dishes": 3, "meat_dishes": 1, "vegetable_dishes": 2}) == 0
    assert composition_gap(spec, {"dishes": 3, "meat_dishes": 0, "vegetable_dishes": 3}) == 2


def test_composition_gap_counts_the_distance_outside_each_range():
    # 3 dishes, at most 1 vegetable: meat [2, 3], vegetable [0, 1].
    spec = PlanSpec(
        mode="shopping", dishes=3, soups=0, vegetable_dishes=1, vegetable_bound="at_most"
    )
    assert composition_gap(spec, {"dishes": 3, "meat_dishes": 2, "vegetable_dishes": 1}) == 0
    assert composition_gap(spec, {"dishes": 3, "meat_dishes": 1, "vegetable_dishes": 2}) == 2
    assert composition_gap(spec, {"dishes": 3, "meat_dishes": 0, "vegetable_dishes": 3}) == 4
    # A dish short: 1 for the total, 1 below the meat range.
    assert composition_gap(spec, {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1}) == 2


def _spec(dishes, **sides):
    return PlanSpec(mode="shopping", dishes=dishes, soups=0 if dishes else 1, **sides)


@pytest.mark.parametrize(
    "spec, meat, vegetable",
    [
        (_spec(8, meat_dishes=2, meat_bound="exact"), (2, 2), (6, 6)),
        (_spec(8, meat_dishes=2), (2, 2), (6, 6)),  # no bound reads as exact
        (_spec(8, meat_dishes=2, meat_bound="at_least"), (2, 8), (0, 6)),
        (_spec(3, vegetable_dishes=1, vegetable_bound="at_most"), (2, 3), (0, 1)),
        (
            _spec(
                4,
                meat_dishes=1,
                meat_bound="at_least",
                vegetable_dishes=2,
                vegetable_bound="at_most",
            ),
            (2, 4),
            (0, 2),
        ),
        (_spec(3, vegetable_dishes=0, vegetable_bound="exact"), (3, 3), (0, 0)),
        # Both named exactly below the total are minimums, with or without the bounds set.
        (
            _spec(
                3,
                meat_dishes=1,
                vegetable_dishes=1,
                meat_bound="at_least",
                vegetable_bound="at_least",
            ),
            (1, 2),
            (1, 2),
        ),
        (_spec(3, meat_dishes=1, vegetable_dishes=1), (1, 2), (1, 2)),
        (_spec(5), (0, 5), (0, 5)),
        (_spec(0), (0, 0), (0, 0)),
    ],
)
def test_side_ranges(spec, meat, vegetable):
    assert side_ranges(spec) == {"meat_dishes": meat, "vegetable_dishes": vegetable}


def test_side_ranges_can_come_out_empty():
    # 2 dishes, at least 3 meat: the meat range is empty (a contradiction asked about).
    low, high = side_ranges(_spec(2, meat_dishes=3, meat_bound="at_least"))["meat_dishes"]
    assert low > high


def test_recipes_using_matches_search_rule(session, seeded):
    belly = make_recipe(session, "红烧肉", lines=[L("五花肉")])
    plain = make_recipe(session, "白菜", lines=[L("小白菜")])
    unlinked = make_recipe(session, "神秘", lines=[L("某种酱", linked=False)])
    ctx = ctx_for(session)
    ids = [belly.id, plain.id, unlinked.id]
    assert recipes_using(session, ctx, "猪肉", ids) == {belly.id}
    assert recipes_using(session, ctx, "某种酱", ids) == {unlinked.id}
    assert recipes_using(session, ctx, "猪肉", []) == set()
