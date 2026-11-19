import pytest
from pydantic import ValidationError

from minemydinner.agent.request import (
    PROMPT_DIR,
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    REPLAN_MESSAGE,
    ParsedRequest,
    apply_defaults,
    default_composition,
    dish_total,
    load_prompt,
    needs_question,
    prompt_files,
    request_problem,
)
from minemydinner.agent.questions import parse_messages, question_text
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
    assert PROMPT_VERSION == "route_v2+parse_v5+planner_v4"
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
    spec, constraints, defaults_used, _ = apply_defaults(parsed())
    assert defaults_used == ["headcount", "dishes", "soups", "mode"]
    assert (spec.mode, spec.dishes, spec.soups, spec.time_limit_min) == ("fridge_only", 1, 0, None)
    assert constraints.unconfirmed_diners == 0 and constraints.diner_ids == ()


def test_stated_values_are_kept_and_not_reported():
    spec, _, defaults_used, _ = apply_defaults(
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
    spec, _, defaults_used, _ = apply_defaults(request_)
    for name, value in spec_counts.items():
        assert getattr(spec, name) == value
    others = {"dishes", "soups", "staples", "desserts", "snacks"} - spec_counts.keys()
    assert all(getattr(spec, name) == 0 for name in others)
    assert defaults_used == defaults


def test_split_without_total_sets_the_total():
    spec, _, defaults_used, _ = apply_defaults(P(headcount=2, meat_dishes=2, mode="shopping"))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (2, 2, None)
    assert (spec.meat_bound, spec.vegetable_bound) == ("exact", None)
    assert spec.soups == 0 and defaults_used == []
    spec, _, _, _ = apply_defaults(P(meat_dishes=1, vegetable_dishes=1))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (2, 1, 1)
    assert (spec.meat_bound, spec.vegetable_bound) == ("exact", "exact")


def test_split_inside_the_total_stays_exact():
    spec, _, _, _ = apply_defaults(P(dishes=8, meat_dishes=2))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (8, 2, None)
    assert (spec.meat_bound, spec.vegetable_bound) == ("exact", None)


def test_named_split_below_the_total_becomes_minimums():
    spec, _, _, _ = apply_defaults(P(dishes=3, meat_dishes=1, vegetable_dishes=1))
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (3, 1, 1)
    assert (spec.meat_bound, spec.vegetable_bound) == ("at_least", "at_least")


def test_parsed_bounds_reach_the_spec():
    spec, _, _, _ = apply_defaults(P(dishes=8, meat_dishes=2, meat_bound="at_least"))
    assert (spec.meat_dishes, spec.meat_bound, spec.vegetable_bound) == (2, "at_least", None)
    spec, _, _, _ = apply_defaults(
        P(dishes=4, meat_dishes=1, meat_bound="at_least", vegetable_dishes=2, vegetable_bound="at_most")
    )
    assert (spec.meat_bound, spec.vegetable_bound) == ("at_least", "at_most")
    # A bound without its count is ignored.
    spec, _, _, _ = apply_defaults(P(dishes=3, meat_bound="at_most"))
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
    _, _, defaults_used, _ = apply_defaults(
        P(headcount=2, dishes=2, mode="shopping", range_fields=["dishes"])
    )
    assert defaults_used == ["dishes"]
    _, _, defaults_used, _ = apply_defaults(P(dishes=2, soups=1, range_fields=["soups", "dishes"]))
    assert defaults_used == ["headcount", "dishes", "soups", "mode"]


def test_a_stated_zero_total_falls_back_to_the_default_composition():
    spec, _, defaults_used, _ = apply_defaults(P(headcount=2, dishes=0, soups=0, mode="shopping"))
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
    spec, _, _, _ = apply_defaults(P(prefer_use=["鸡蛋"], must_use=["番茄"], all_use=["葱"], dishes=2))
    assert (spec.prefer_use, spec.must_use, spec.all_use) == (["鸡蛋"], ["番茄"], ["葱"])
    spec, _, _, _ = apply_defaults(P(dishes=2))
    assert spec.prefer_use == spec.must_use == spec.all_use == []


def test_parsed_request_limits():
    with pytest.raises(ValidationError):
        P(must_use=[str(i) for i in range(11)])
    with pytest.raises(ValidationError):
        P(dishes=-1)


def test_other_people_are_unconfirmed_until_diner_selection():
    _, constraints, defaults_used, _ = apply_defaults(
        parsed(others_mentioned=True, headcount=3), self_diner_id=7
    )
    assert constraints.diner_ids == (7,) and constraints.guest_rules == ()
    assert constraints.unconfirmed_diners == 2
    assert "headcount" not in defaults_used


def test_apply_defaults_headcount_from_card():
    spec, constraints, defaults_used, headcount = apply_defaults(
        parsed(others_mentioned=True), self_diner_id=7, headcount=3
    )
    assert headcount == 3
    assert (spec.dishes, spec.soups) == (2, 1)
    assert "headcount" not in defaults_used
    assert constraints.unconfirmed_diners == 2
    # The card's headcount wins over the parse's.
    _, _, _, headcount = apply_defaults(parsed(headcount=2), headcount=4)
    assert headcount == 4
    _, _, defaults_used, headcount = apply_defaults(parsed())
    assert headcount == 1 and "headcount" in defaults_used


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
    spec, _, _, _ = apply_defaults(
        P(headcount=2, dishes=0, meat_dishes=0, vegetable_dishes=0, mode="shopping")
    )
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (2, None, None)
    assert (spec.meat_bound, spec.vegetable_bound) == (None, None)


def test_range_fields_of_unstated_counts_are_ignored():
    _, _, defaults_used, _ = apply_defaults(
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
    spec, _, defaults_used, _ = apply_defaults(request)
    assert (spec.dishes, spec.soups) == (2, 0)
    assert (spec.vegetable_dishes, spec.vegetable_bound) == (1, "at_most")
    assert defaults_used == ["dishes"]


def test_dish_total_is_raised_to_the_lower_bounds():
    request = P(headcount=1, meat_dishes=2, meat_bound="at_least", mode="shopping")
    assert dish_total(request, 1) == 2
    spec, _, defaults_used, _ = apply_defaults(request)
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
    spec, _, defaults_used, _ = apply_defaults(P(soups=1, range_fields=["soups"]))
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
    spec, _, defaults_used, _ = apply_defaults(request)
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


def test_planner_v3_is_unchanged_and_v4_differs_only_in_the_note_rule():
    import hashlib

    assert PROMPT_VERSIONS["planner"] == "planner_v4"
    v3 = (PROMPT_DIR / "planner_v3.md").read_bytes().replace(b"\r\n", b"\n")
    # The committed planner_v3.md (a version records used) must never change.
    assert hashlib.sha256(v3).hexdigest() == (
        "123206bf2e1030911e6c7ecf80a0854d9336fc1c110e2c8715f6232b7c89fa1e"
    )
    v4 = (PROMPT_DIR / "planner_v4.md").read_text(encoding="utf-8")
    assert "Do not count or list" in v4 and "one sentence" in v4


def test_prompt_versions_label():
    assert PROMPT_VERSION == "route_v2+parse_v5+planner_v4"
    assert PROMPT_VERSIONS["parse"] == "parse_v5"


def test_parse_v4_is_frozen_and_parse_v5_changes_only_the_cold_dish_rule():
    import hashlib

    v4 =(PROMPT_DIR / "parse_v4.md").read_bytes().replace(b"\r\n", b"\n")
    # parse_v4 was used by evaluation round 8 (2026-11-18), so it must never change.
    assert hashlib.sha256(v4).hexdigest() == (
        "d5544c3033136c757d740f7818275c2ac732cbbb4c48bc8b89e5eb83fd33345f"
    )
    old = v4.decode("utf-8").splitlines()
    new = load_prompt("parse").splitlines()
    changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    assert len(old) == len(new) and len(changed) == 1
    assert "就做两个凉菜" in new[changed[0]] and "来个凉菜" in new[changed[0]]


def test_parse_prompt_is_no_longer_than_parse_v3():
    v3 = (PROMPT_DIR / "parse_v3.md").read_text(encoding="utf-8").splitlines()
    current = load_prompt("parse")
    assert len(current.splitlines()) <= len(v3) == 79
    for field in ("guests", "friend_rules", "self_rules", "diet_words"):
        assert field in current
    assert "三个菜，不要素菜" in current and REPLAN_MESSAGE["zh"] in current


def test_parsed_request_drops_unknown_allergen_values():
    request = ParsedRequest.model_validate(
        {"guests": [{"name": "Alex", "allergens": ["peanut", "mango"]}]}
    )
    assert request.guests[0].name == "Alex"
    assert request.guests[0].allergens == ["peanut"]


def test_parsed_request_stated_rules_shape():
    request = ParsedRequest.model_validate(
        {
            "friend_rules": [
                {"code": "Diner 2", "avoid": [{"name": "羊肉", "kind": "restriction"}]}
            ],
            "self_rules": {"avoid": [{"name": "芒果", "kind": "restriction"}]},
            "guests": [{"diet_words": ["吃素"]}],
        }
    )
    assert request.friend_rules[0].code == "Diner 2"
    assert request.friend_rules[0].avoid[0].kind == "restriction"
    assert request.self_rules.avoid[0].name == "芒果" and request.self_rules.allergens == []
    assert request.guests[0].name is None and request.guests[0].diet_words == ["吃素"]
    with pytest.raises(ValidationError):
        ParsedRequest.model_validate({"self_rules": {"avoid": [{"name": "x", "kind": "maybe"}]}})
    with pytest.raises(ValidationError):
        ParsedRequest.model_validate({"guests": [{}] * 11})


def test_parsed_request_old_shape_still_loads():
    old = {
        "others_mentioned": True, "headcount": 2, "dishes": 2, "soups": 1,
        "meat_dishes": None, "vegetable_dishes": None, "meat_bound": None,
        "vegetable_bound": None, "staples": None, "desserts": None, "snacks": None,
        "range_fields": [], "mode": "fridge_only", "time_limit_min": None, "query": None,
        "must_use": [], "prefer_use": [], "all_use": [], "exclude_recent_days": None,
    }
    request = ParsedRequest.model_validate(old)
    assert request.guests == [] and request.friend_rules == []
    assert request.self_rules is None
    assert request.headcount == 2 and request.mode == "fridge_only"


def _chat_strings(locale: str) -> dict:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "frontend" / "messages" / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))["chat"]


# MessageList joins the field strings with Intl.ListFormat, type disjunction.
LIST_OR = {"zh": "或", "en": " or "}


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_question_text_matches_chat_wording(locale):
    chat = _chat_strings(locale)
    strings = chat["question"]
    hint = strings["hint"]
    for name in ("mode", "composition"):
        value = {"kind": "question", "fields": [name]}
        assert question_text(value, locale) == hint.replace("{fields}", strings[name])
    both = {"kind": "question", "fields": ["mode", "composition"]}
    joined = strings["mode"] + LIST_OR[locale] + strings["composition"]
    assert question_text(both, locale) == hint.replace("{fields}", joined)
    for problem, text in chat["problem"].items():
        value = {"kind": "question", "fields": ["composition"], "problem": problem}
        assert question_text(value, locale) == text
    assert question_text({"kind": "request_style"}, locale) is None
    assert question_text({"kind": "diners", "people": []}, locale) is None


def test_question_text_falls_back_to_english():
    value = {"kind": "question", "fields": ["mode"]}
    assert question_text(value, "fr") == question_text(value, "en")


def test_parse_messages_interleaves_questions():
    from langchain_core.messages import HumanMessage

    messages = [HumanMessage("今晚做饭"), HumanMessage("两个")]
    asked = {"kind": "question", "fields": ["composition"]}
    questions = [
        {"at": 1, "value": {"kind": "request_style"}},
        {"at": 1, "value": asked},
    ]
    out = parse_messages(messages, questions, "zh")
    assert [m.type for m in out] == ["human", "ai", "human"]
    assert out[0] is messages[0] and out[2] is messages[1]
    assert out[1].content == question_text(asked, "zh")
    # A question with no answer yet comes last; the input list is not changed.
    out = parse_messages(messages, [{"at": 2, "value": asked}], "zh")
    assert [m.type for m in out] == ["human", "human", "ai"]
    assert len(messages) == 2
    assert parse_messages(messages, [], "zh") == messages
