"""The next-action buttons change the card's request in code (plan 3b-1b spec 5.5)."""

import pytest

from minemydinner.agent.actions import ACTIONS, action_of, apply_action, spec_changes
from minemydinner.agent.request import ACTION_MESSAGES, ParsedRequest, request_problem

REQUEST = {
    "mode": "fridge_only",
    "dishes": 3,
    "meat_dishes": 2,
    "meat_bound": "exact",
    "time_limit_min": 30,
    "all_use": ["鸡蛋"],
    "must_use": [],
    "prefer_use": [],
    "query": "清淡点",
    "meal_avoid": ["牛肉"],
    "counts_stated_now": False,
}


def test_apply_action_each():
    shopping = apply_action(REQUEST, "switch_to_shopping")
    assert shopping["mode"] == "shopping"
    fewer = apply_action(REQUEST, "fewer_dishes")
    assert (fewer["dishes"], fewer["meat_dishes"]) == (2, 2)
    fewer = apply_action(fewer, "fewer_dishes")
    assert (fewer["dishes"], fewer["meat_dishes"], fewer["meat_bound"]) == (1, 1, "exact")
    assert apply_action(fewer, "fewer_dishes")["dishes"] == 1
    assert apply_action(REQUEST, "drop_time_limit")["time_limit_min"] is None
    relaxed = apply_action(REQUEST, "relax_split")
    assert relaxed["dishes"] == 3
    assert [relaxed[n] for n in ("meat_dishes", "meat_bound")] == [None, None]
    assert [relaxed[n] for n in ("vegetable_dishes", "vegetable_bound")] == [None, None]
    named = apply_action(REQUEST, "relax_named")
    assert (named["must_use"], named["all_use"], named["prefer_use"]) == ([], [], ["鸡蛋"])
    for action in ACTIONS:
        changed = apply_action(REQUEST, action)
        # The rest of the request stays, the one-meal avoidance and the wish included.
        assert changed["query"] == "清淡点" and changed["meal_avoid"] == ["牛肉"]
        assert isinstance(changed["counts_stated_now"], bool)
    # The input is never changed.
    assert REQUEST["dishes"] == 3 and REQUEST["all_use"] == ["鸡蛋"]


def test_counts_stated_now_follows_the_action():
    # A count change states the counts; any other action keeps what the turn stated.
    assert apply_action(REQUEST, "fewer_dishes")["counts_stated_now"] is True
    assert apply_action(REQUEST, "relax_split")["counts_stated_now"] is True
    for action in ("switch_to_shopping", "drop_time_limit", "relax_named"):
        assert apply_action(REQUEST, action)["counts_stated_now"] is False
        stated = {**REQUEST, "counts_stated_now": True}
        assert apply_action(stated, action)["counts_stated_now"] is True
        assert apply_action({**REQUEST, "counts_stated_now": None}, action)[
            "counts_stated_now"
        ] is False


def test_totals_come_from_the_spec_when_the_request_has_none():
    split = {"meat_dishes": 1, "vegetable_dishes": 1, "mode": "fridge_only"}
    spec = {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1}
    relaxed = apply_action(split, "relax_split", spec)
    assert relaxed["dishes"] == 2 and relaxed["meat_dishes"] is None
    # Without a spec the total is the request's own ("一荤一素" is 2).
    assert apply_action(split, "relax_split")["dishes"] == 2
    fewer = apply_action(split, "fewer_dishes", spec)
    assert (fewer["dishes"], fewer["meat_dishes"], fewer["vegetable_dishes"]) == (1, 1, 1)
    # Two exact sides above the new total would contradict it: each becomes "at most".
    assert (fewer["meat_bound"], fewer["vegetable_bound"]) == ("at_most", "at_most")
    assert request_problem(ParsedRequest.model_validate(fewer)) is None
    # A request with no count at all takes every count from the spec, so a default soup stays.
    defaults = {"dishes": 2, "soups": 1, "staples": 0, "desserts": 0, "snacks": 0}
    fewer = apply_action({"mode": "shopping"}, "fewer_dishes", defaults)
    assert (fewer["dishes"], fewer["soups"]) == (1, 1)
    # A range field the button sets is no longer a default.
    ranged = {"dishes": 3, "range_fields": ["dishes", "soups"]}
    assert apply_action(ranged, "fewer_dishes")["range_fields"] == ["soups"]


def test_spec_changes_clear_what_a_revision_cannot():
    assert spec_changes(REQUEST, "drop_time_limit") == {"time_limit_min": None}
    named = apply_action(REQUEST, "relax_named")
    assert spec_changes(named, "relax_named") == {
        "must_use": [], "all_use": [], "prefer_use": ["鸡蛋"]
    }
    assert spec_changes(REQUEST, "switch_to_shopping") == {}


@pytest.mark.parametrize(
    ("text", "action"),
    [(text, action) for texts in ACTION_MESSAGES.values() for action, text in texts.items()],
)
def test_action_of_reads_each_fixed_message(text, action):
    assert action_of(f" {text} ") == action
    assert action_of("想吃点海鲜") is None


def test_on_plan_counts_takes_the_plan_shown():
    from minemydinner.agent.actions import on_plan_counts

    plan = {
        "recipes": [
            {"dish_type": "meat dish"},
            {"dish_type": "vegetable dish"},
            {"dish_type": "vegetable dish"},
            {"dish_type": "meat dish"},
        ]
    }
    rebased = on_plan_counts({**REQUEST, "soups": 1, "range_fields": ["dishes"]}, plan)
    assert (rebased["dishes"], rebased["soups"]) == (4, 0)
    assert (rebased["meat_dishes"], rebased["vegetable_dishes"]) == (2, 2)
    assert (rebased["meat_bound"], rebased["vegetable_bound"]) == ("exact", "exact")
    assert rebased["range_fields"] == [] and rebased["query"] == "清淡点"
    assert on_plan_counts(REQUEST, {"recipes": []}) == REQUEST
