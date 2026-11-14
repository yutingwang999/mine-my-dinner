import pytest

from minemydinner.agent.request import (
    PROMPT_VERSION,
    ParsedRequest,
    apply_defaults,
    default_composition,
    needs_question,
)


def parsed(**kwargs) -> ParsedRequest:
    return ParsedRequest(**kwargs)


@pytest.mark.parametrize(
    "n, expected", [(1, (1, 0)), (2, (2, 0)), (3, (2, 1)), (4, (2, 1)), (5, (3, 1)), (9, (3, 1))]
)
def test_default_composition(n, expected):
    assert default_composition(n) == expected


def test_prompt_version_is_plan_v1():
    assert PROMPT_VERSION == "plan_v1"


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


def test_composition_default_fills_only_the_unstated_part():
    spec, _, defaults_used = apply_defaults(parsed(headcount=4, dishes=3, mode="shopping"))
    assert (spec.dishes, spec.soups) == (3, 1)
    assert defaults_used == ["soups"]
    spec, _, defaults_used = apply_defaults(parsed(headcount=1, dishes=2, mode="shopping"))
    assert (spec.dishes, spec.soups) == (2, 0)
    assert defaults_used == ["soups"]
    _, _, defaults_used = apply_defaults(parsed(headcount=4, soups=1, mode="shopping"))
    assert defaults_used == ["dishes"]


def test_a_stated_zero_by_zero_falls_back_to_the_default_composition():
    spec, _, defaults_used = apply_defaults(
        parsed(headcount=2, dishes=0, soups=0, mode="shopping")
    )
    assert (spec.dishes, spec.soups) == (2, 0)
    assert defaults_used == ["dishes", "soups"]
    spec, _, _ = apply_defaults(parsed(headcount=1, dishes=0, mode="shopping"))
    assert spec.dishes + spec.soups >= 1


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


@pytest.mark.parametrize("style", ["plan_then_adjust", None])
def test_other_styles_never_ask(style):
    assert needs_question(parsed(), style) == []
