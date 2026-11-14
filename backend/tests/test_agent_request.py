import pytest
from pydantic import ValidationError

from minemydinner.agent.request import (
    PROMPT_DIR,
    PROMPT_VERSION,
    ParsedRequest,
    apply_defaults,
    default_composition,
    needs_question,
    request_problem,
)
from minemydinner.planning.validate import PlanSpec


def parsed(**kwargs) -> ParsedRequest:
    return ParsedRequest(**kwargs)


P = parsed


@pytest.mark.parametrize(
    "n, expected", [(1, (1, 0)), (2, (2, 0)), (3, (2, 1)), (4, (2, 1)), (5, (3, 1)), (9, (3, 1))]
)
def test_default_composition(n, expected):
    assert default_composition(n) == expected


def test_prompt_version_is_plan_v2():
    assert PROMPT_VERSION == "plan_v2"
    for name in ("parse.md", "route.md", "planner.md"):
        assert (PROMPT_DIR / name).exists()


def test_apply_defaults_reports_each_default():
    spec, constraints, defaults_used = apply_defaults(parsed())
    assert defaults_used == ["headcount", "dishes", "soups", "mode"]
    assert (spec.mode, spec.dishes, spec.soups, spec.time_limit_min) == ("fridge_only", 1, 0, None)
    assert constraints.unconfirmed_diners == 0 and constraints.diner_ids == ()


def test_stated_values_are_kept_and_not_reported():
    spec, _, defaults_used = apply_defaults(
        parsed(headcount=2, dishes=3, soups=1, mode="shopping", time_limit_min=30)
    )
    assert defaults_used == []
    assert (spec.mode, spec.dishes, spec.soups, spec.time_limit_min) == ("shopping", 3, 1, 30)


@pytest.mark.parametrize(
    "request_, spec_counts, defaults",
    [
        (P(headcount=3), dict(dishes=2, soups=1), ["dishes", "soups", "mode"]),
        (P(headcount=3, dishes=2), dict(dishes=2, soups=0), ["mode"]),
        (P(staples=1), dict(dishes=0, soups=0, staples=1), ["headcount", "mode"]),
        (
            P(dishes=2, soups=1, staples=1),
            dict(dishes=2, soups=1, staples=1),
            ["headcount", "mode"],
        ),
        (P(desserts=1), dict(dishes=0, soups=0, desserts=1), ["headcount", "mode"]),
        (P(headcount=1, snacks=2), dict(dishes=0, snacks=2), ["mode"]),
    ],
)
def test_defaults_apply_only_when_no_count_was_stated(request_, spec_counts, defaults):
    spec, _, defaults_used = apply_defaults(request_)
    for name, value in spec_counts.items():
        assert getattr(spec, name) == value
    others = {"dishes", "soups", "staples", "desserts", "snacks"} - spec_counts.keys()
    assert all(getattr(spec, name) == 0 for name in others)
    assert defaults_used == defaults


def test_split_without_total_sets_the_total():
    spec, _, defaults_used = apply_defaults(P(headcount=2, meat_dishes=2, mode="shopping"))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (2, 2, None)
    assert spec.split_is_minimum is False and spec.soups == 0 and defaults_used == []
    spec, _, _ = apply_defaults(P(meat_dishes=1, vegetable_dishes=1))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (2, 1, 1)


def test_split_inside_the_total_stays_exact():
    spec, _, _ = apply_defaults(P(dishes=8, meat_dishes=2))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (8, 2, None)
    assert spec.split_is_minimum is False


def test_named_split_below_the_total_becomes_minimums():
    spec, _, _ = apply_defaults(P(dishes=3, meat_dishes=1, vegetable_dishes=1))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (3, 1, 1)
    assert spec.split_is_minimum is True


def test_split_above_the_total_is_a_problem():
    assert request_problem(P(dishes=2, meat_dishes=3)) == "split_exceeds_total"
    assert request_problem(P(dishes=2, meat_dishes=2, vegetable_dishes=1)) == "split_exceeds_total"
    assert request_problem(P(dishes=2, meat_dishes=1, vegetable_dishes=1)) is None
    assert request_problem(P(dishes=3, meat_dishes=1, vegetable_dishes=1)) is None
    assert request_problem(P(meat_dishes=2)) is None
    assert request_problem(P()) is None


def test_more_than_twelve_is_a_problem():
    assert request_problem(P(dishes=8, soups=2, staples=2)) is None
    assert request_problem(P(dishes=8, soups=2, staples=2, snacks=1)) == "too_many"
    assert request_problem(P(meat_dishes=7, vegetable_dishes=6)) == "too_many"


def test_range_fields_are_reported_as_assumed():
    _, _, defaults_used = apply_defaults(
        P(headcount=2, dishes=2, mode="shopping", range_fields=["dishes"])
    )
    assert defaults_used == ["dishes"]
    _, _, defaults_used = apply_defaults(P(dishes=2, soups=1, range_fields=["soups", "dishes"]))
    assert defaults_used == ["headcount", "dishes", "soups", "mode"]


def test_a_stated_zero_total_falls_back_to_the_default_composition():
    spec, _, defaults_used = apply_defaults(P(headcount=2, dishes=0, soups=0, mode="shopping"))
    assert (spec.dishes, spec.soups) == (2, 0)
    assert defaults_used == ["dishes", "soups"]


def test_plan_spec_rejects_zero_and_over_twelve():
    with pytest.raises(ValidationError):
        PlanSpec(mode="shopping", dishes=0, soups=0)
    with pytest.raises(ValidationError):
        PlanSpec(mode="shopping", dishes=8, soups=2, staples=2, snacks=1)
    assert PlanSpec(mode="shopping", dishes=0, soups=0, staples=1).staples == 1
    assert PlanSpec(mode="shopping", dishes=8, soups=2, staples=2).dishes == 8


def test_named_ingredient_lists_reach_the_spec():
    spec, _, _ = apply_defaults(P(prefer_use=["鸡蛋"], must_use=["番茄"], all_use=["葱"], dishes=2))
    assert (spec.prefer_use, spec.must_use, spec.all_use) == (["鸡蛋"], ["番茄"], ["葱"])
    spec, _, _ = apply_defaults(P(dishes=2))
    assert spec.prefer_use == spec.must_use == spec.all_use == []


def test_parsed_request_limits():
    with pytest.raises(ValidationError):
        P(must_use=[str(i) for i in range(11)])
    with pytest.raises(ValidationError):
        P(dishes=-1)


def test_other_people_are_unconfirmed_until_diner_selection():
    _, constraints, defaults_used = apply_defaults(
        parsed(others_mentioned=True, headcount=3), self_diner_id=7
    )
    assert constraints.diner_ids == (7,) and constraints.guest_rules == ()
    assert constraints.unconfirmed_diners == 2
    assert "headcount" not in defaults_used


def test_ask_first_asks_only_for_unstated():
    assert needs_question(parsed(), "ask_first") == ["mode", "composition"]
    assert needs_question(parsed(mode="shopping"), "ask_first") == ["composition"]
    assert needs_question(parsed(dishes=2), "ask_first") == ["mode"]
    assert needs_question(parsed(dishes=2, mode="fridge_only"), "ask_first") == []


def test_ask_first_asks_about_composition_only_without_any_count():
    assert needs_question(P(mode="shopping"), "ask_first") == ["composition"]
    for field in ("soups", "meat_dishes", "vegetable_dishes", "staples", "desserts", "snacks"):
        assert needs_question(P(mode="shopping", **{field: 1}), "ask_first") == []


@pytest.mark.parametrize("style", ["plan_then_adjust", None])
def test_other_styles_never_ask(style):
    assert needs_question(parsed(), style) == []


def test_zero_total_fallback_drops_the_stated_split():
    spec, _, _ = apply_defaults(
        P(headcount=2, dishes=0, meat_dishes=0, vegetable_dishes=0, mode="shopping")
    )
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (2, None, None)
    assert spec.split_is_minimum is False


def test_range_fields_of_unstated_counts_are_ignored():
    _, _, defaults_used = apply_defaults(
        P(headcount=2, dishes=2, mode="shopping", range_fields=["soups", "dishes"])
    )
    assert defaults_used == ["dishes"]
