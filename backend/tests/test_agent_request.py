import pytest
from pydantic import ValidationError

from minemydinner.agent.request import (
    PROMPT_DIR,
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    ParsedRequest,
    apply_defaults,
    default_composition,
    dish_total,
    load_prompt,
    needs_question,
    prompt_files,
    request_problem,
)
from minemydinner.planning.composition import side_ranges
from minemydinner.planning.validate import PlanSpec


def parsed(**kwargs) -> ParsedRequest:
    return ParsedRequest(**kwargs)


P = parsed


@pytest.mark.parametrize(
    "n, expected", [(1, (1, 0)), (2, (2, 0)), (3, (2, 1)), (4, (2, 1)), (5, (3, 1)), (9, (3, 1))]
)
def test_default_composition(n, expected):
    assert default_composition(n) == expected


def test_prompt_versions_name_existing_files():
    assert list(PROMPT_VERSIONS) == ["route", "parse", "planner"]
    assert PROMPT_VERSION == "+".join(PROMPT_VERSIONS.values())
    assert PROMPT_VERSION == "route_v2+parse_v3+planner_v3"
    for kind, version in PROMPT_VERSIONS.items():
        assert version.startswith(f"{kind}_v")
        assert (PROMPT_DIR / f"{version}.md").exists()
        assert load_prompt(kind)
    assert [p.name for p in prompt_files()] == [f"{v}.md" for v in PROMPT_VERSIONS.values()]


def test_package_data_glob_matches_every_prompt_file():
    import tomllib
    from fnmatch import fnmatch
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["minemydinner.agent"]
    for path in prompt_files():
        relative = path.relative_to(PROMPT_DIR.parent).as_posix()
        assert any(fnmatch(relative, pattern) for pattern in patterns), relative


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
    assert (spec.meat_bound, spec.vegetable_bound) == ("exact", None)
    assert spec.soups == 0 and defaults_used == []
    spec, _, _ = apply_defaults(P(meat_dishes=1, vegetable_dishes=1))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (2, 1, 1)
    assert (spec.meat_bound, spec.vegetable_bound) == ("exact", "exact")


def test_split_inside_the_total_stays_exact():
    spec, _, _ = apply_defaults(P(dishes=8, meat_dishes=2))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (8, 2, None)
    assert (spec.meat_bound, spec.vegetable_bound) == ("exact", None)


def test_named_split_below_the_total_becomes_minimums():
    spec, _, _ = apply_defaults(P(dishes=3, meat_dishes=1, vegetable_dishes=1))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (3, 1, 1)
    assert (spec.meat_bound, spec.vegetable_bound) == ("at_least", "at_least")


def test_parsed_bounds_reach_the_spec():
    spec, _, _ = apply_defaults(P(dishes=8, meat_dishes=2, meat_bound="at_least"))
    assert (spec.meat_dishes, spec.meat_bound, spec.vegetable_bound) == (2, "at_least", None)
    spec, _, _ = apply_defaults(
        P(dishes=4, meat_dishes=1, meat_bound="at_least", vegetable_dishes=2, vegetable_bound="at_most")
    )
    assert (spec.meat_bound, spec.vegetable_bound) == ("at_least", "at_most")
    # A bound without its count is ignored.
    spec, _, _ = apply_defaults(P(dishes=3, meat_bound="at_most"))
    assert (spec.meat_dishes, spec.meat_bound) == (None, None)


def test_split_above_the_total_is_a_problem():
    assert request_problem(P(dishes=2, meat_dishes=3)) == "split_conflicts_total"
    assert request_problem(P(dishes=2, meat_dishes=2, vegetable_dishes=1)) == "split_conflicts_total"
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
    assert (spec.meat_bound, spec.vegetable_bound) == (None, None)


def test_range_fields_of_unstated_counts_are_ignored():
    _, _, defaults_used = apply_defaults(
        P(headcount=2, dishes=2, mode="shopping", range_fields=["soups", "dishes"])
    )
    assert defaults_used == ["dishes"]


# ---- design 5.11: bounds, the dish total, and contradictions ----


def test_dish_total_keeps_a_stated_total():
    assert dish_total(P(dishes=8, meat_dishes=2, meat_bound="at_least"), 1) == 8
    assert dish_total(P(dishes=3, vegetable_dishes=1, vegetable_bound="at_most"), 5) == 3


def test_dish_total_of_an_exact_split_is_its_sum():
    # "一荤一素"
    assert dish_total(P(meat_dishes=1, vegetable_dishes=1), 4) == 2
    assert dish_total(P(meat_dishes=1, meat_bound="exact", vegetable_dishes=1), 4) == 2
    # "两个荤菜"
    assert dish_total(P(meat_dishes=2), 1) == 2


def test_dish_total_with_a_bound_and_no_total_uses_the_headcount():
    # "三个人，最多一个素菜": the default 2 dishes, assumed, no soup added.
    request = P(headcount=3, vegetable_dishes=1, vegetable_bound="at_most", mode="shopping")
    assert dish_total(request, 3) == 2
    spec, _, defaults_used = apply_defaults(request)
    assert (spec.dishes, spec.soups) == (2, 0)
    assert (spec.vegetable_dishes, spec.vegetable_bound) == (1, "at_most")
    assert defaults_used == ["dishes"]


def test_dish_total_is_raised_to_the_lower_bounds():
    request = P(headcount=1, meat_dishes=2, meat_bound="at_least", mode="shopping")
    assert dish_total(request, 1) == 2
    spec, _, defaults_used = apply_defaults(request)
    assert (spec.dishes, spec.soups, spec.meat_dishes) == (2, 0, 2)
    assert defaults_used == ["dishes"]


@pytest.mark.parametrize(
    "request_",
    [
        # 两个菜，至少三个荤菜
        P(dishes=2, meat_dishes=3, meat_bound="at_least"),
        # 三个菜，最多一个荤菜，最多一个素菜
        P(
            dishes=3,
            meat_dishes=1,
            meat_bound="at_most",
            vegetable_dishes=1,
            vegetable_bound="at_most",
        ),
        # 两个菜，三个荤菜
        P(dishes=2, meat_dishes=3, meat_bound="exact"),
    ],
)
def test_an_empty_side_range_is_a_conflict(request_):
    assert request_problem(request_) == "split_conflicts_total"


def test_bounds_inside_the_total_are_no_problem():
    assert request_problem(P(dishes=8, meat_dishes=2, meat_bound="at_least")) is None
    assert request_problem(P(dishes=3, vegetable_dishes=1, vegetable_bound="at_most")) is None
    assert request_problem(P(dishes=3, vegetable_dishes=0, vegetable_bound="exact")) is None
    assert request_problem(P(headcount=3, vegetable_dishes=1, vegetable_bound="at_most")) is None


def test_at_least_one_soup_is_one_soup_assumed():
    # "至少一个汤"
    spec, _, defaults_used = apply_defaults(P(soups=1, range_fields=["soups"]))
    assert (spec.soups, spec.dishes) == (1, 0)
    assert "soups" in defaults_used


def test_old_spec_with_split_is_minimum_reads_as_bounds():
    old = PlanSpec.model_validate(
        {"mode": "shopping", "dishes": 3, "soups": 0, "meat_dishes": 1, "vegetable_dishes": 1,
         "split_is_minimum": True}
    )
    assert (old.meat_bound, old.vegetable_bound) == ("at_least", "at_least")
    exact = PlanSpec.model_validate(
        {"mode": "shopping", "dishes": 8, "soups": 0, "meat_dishes": 2, "split_is_minimum": False}
    )
    assert (exact.meat_bound, exact.vegetable_bound) == ("exact", None)


def test_dish_total_is_lowered_to_the_upper_bounds():
    # 五个人，最多一个荤菜，最多一个素菜: the default 3 dishes is lowered to 2.
    request = P(
        headcount=5,
        meat_dishes=1,
        meat_bound="at_most",
        vegetable_dishes=1,
        vegetable_bound="at_most",
        mode="shopping",
    )
    assert dish_total(request, 5) == 2
    assert request_problem(request) is None
    spec, _, defaults_used = apply_defaults(request)
    assert (spec.dishes, spec.soups) == (2, 0)
    assert side_ranges(spec) == {"meat_dishes": (1, 1), "vegetable_dishes": (1, 1)}
    assert defaults_used == ["dishes"]


def test_dish_total_with_one_upper_bound_is_not_lowered():
    # 五个人，最多一个素菜: the meat side has no upper bound, so the default 3 stays.
    request = P(headcount=5, vegetable_dishes=1, vegetable_bound="at_most")
    assert dish_total(request, 5) == 3
    # An exact side and an at-least side: no upper bound on the second.
    request = P(headcount=5, meat_dishes=1, vegetable_dishes=1, vegetable_bound="at_least")
    assert dish_total(request, 5) == 3


def test_an_exact_side_counts_as_an_upper_bound():
    request = P(headcount=5, meat_dishes=1, vegetable_dishes=1, vegetable_bound="at_most")
    assert dish_total(request, 5) == 2


def test_a_stated_total_is_never_clamped():
    # 三个菜，最多一个荤菜，最多一个素菜 stays a contradiction.
    request = P(
        dishes=3,
        meat_dishes=1,
        meat_bound="at_most",
        vegetable_dishes=1,
        vegetable_bound="at_most",
    )
    assert dish_total(request, 5) == 3
    assert request_problem(request) == "split_conflicts_total"


def test_both_sides_at_most_zero_is_a_contradiction():
    request = P(
        headcount=3,
        meat_dishes=0,
        meat_bound="at_most",
        vegetable_dishes=0,
        vegetable_bound="at_most",
    )
    assert dish_total(request, 3) == 2
    assert request_problem(request) == "split_conflicts_total"
