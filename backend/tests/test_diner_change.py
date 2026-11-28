"""Counts and "make it again" when who eats changes (plan 3b-2 spec 5.3 and 5.4): pure rules."""

from minemydinner.agent.diner_change import (
    message_sets_counts,
    moved_counts,
    must_change,
    people_constraints,
    remakes,
)
from minemydinner.agent.follow_up import FollowUpReading, ReadAdd, ReadCounts, ReadRemoval


def _present(**over) -> dict[str, int]:
    base = {
        "dishes": 0,
        "meat_dishes": 0,
        "vegetable_dishes": 0,
        "soups": 0,
        "staples": 0,
        "desserts": 0,
        "snacks": 0,
    }
    return {**base, **over}


def test_moved_counts_from_two_to_four():
    assert moved_counts(2, 4, _present(dishes=4)) == {"soups": 1}


def test_moved_counts_from_four_to_five():
    assert moved_counts(4, 5, _present(dishes=2, soups=1)) == {"dishes": 3}


def test_moved_counts_equal_defaults():
    assert moved_counts(3, 4, _present(dishes=2, soups=1)) == {}


def test_moved_counts_floors():
    assert moved_counts(5, 1, _present(dishes=1, soups=0)) == {"dishes": 1, "soups": 0}
    assert moved_counts(5, 2, _present(dishes=2, soups=1)) == {"dishes": 1, "soups": 0}


def test_moved_counts_never_adds_a_dish_when_people_leave():
    """A plan with no meat or vegetable dish keeps none when people leave: the floor is the
    plan's own count when that is below one."""
    assert moved_counts(5, 2, _present(soups=1, staples=1)) == {"dishes": 0, "soups": 0}


def test_message_sets_counts():
    assert message_sets_counts(FollowUpReading(kind="people", add=[ReadAdd(dish_type="soup")]))
    assert message_sets_counts(FollowUpReading(kind="people", counts=ReadCounts(soups=1)))
    assert not message_sets_counts(
        FollowUpReading(kind="people", remove=[ReadRemoval(id=1, mode="drop", quote="no")])
    )
    assert not message_sets_counts(FollowUpReading(kind="people", wish="lighter"))


def test_must_change_and_remakes():
    present = _present(dishes=5)
    breaking = {1: "diner_rule", 2: "meal_avoid"}
    types = {1: "staple food", 2: "dessert"}
    must = must_change(breaking, present, {"dishes": 4}, types)
    assert must == 3 and remakes(5, must)
    assert not remakes(5, must_change(breaking, present, {}, types))
    assert must_change({1: "stock", 2: "time_limit"}, present, {}, types) == 0


def test_must_change_counts_a_dropped_breaker_once():
    types = {1: "meat dish", 2: "vegetable dish", 3: "meat dish", 4: "vegetable dish"}
    breaking = {1: "diner_rule", 2: "diner_rule"}
    must = must_change(breaking, _present(dishes=4), {"dishes": 3}, types)
    assert must == 2 and not remakes(4, must)
    # Soups are counted the same way; a breaker of another type adds on top.
    soups = {1: "soup", 2: "staple food"}
    assert must_change({1: "diner_rule", 2: "diner_rule"}, _present(soups=1), {"soups": 0}, soups) == 2


def test_people_constraints():
    old = {"diner_ids": [1], "guest_rules": [], "unconfirmed_diners": 0, "headcount": 1}
    rule = {
        "diner_key": "guest:1",
        "rule_type": "allergy",
        "allergen": "peanut",
        "ingredient_id": None,
        "text": None,
    }
    settled = {
        "diner_ids": [1, 2],
        "meal_rules": [rule],
        "guest_names": {},
        "others": 2,
        "others_answer": "unsure",
        "headcount": 5,
        "stated_headcount": 5,
    }
    assert people_constraints(settled, old) == {
        "diner_ids": [1, 2],
        "guest_rules": [rule],
        "unconfirmed_diners": 2,
        "headcount": 5,
    }
    assert people_constraints(None, old) is old
