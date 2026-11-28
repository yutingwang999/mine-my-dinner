import re

import pytest
from pydantic import ValidationError

from minemydinner.agent.request import (
    PROMPT_DIR,
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    ACTION_MESSAGES,
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
    # Plan 3b-2 Task 8: no step loads a revise prompt, so revise_v4 left the label.
    assert list(PROMPT_VERSIONS) == ["route", "parse", "planner", "follow_up", "pick"]
    assert PROMPT_VERSION == "+".join(PROMPT_VERSIONS.values())
    assert PROMPT_VERSION == "route_v6+parse_v8+planner_v5+follow_up_v7+pick_v6"
    for kind, version in PROMPT_VERSIONS.items():
        assert version.startswith(f"{kind}_v")
        assert (PROMPT_DIR / f"{version}.md").exists()
        assert load_prompt(kind)
    assert [p.name for p in prompt_files()] == [f"{v}.md" for v in PROMPT_VERSIONS.values()]


def test_route_v6_and_revise_v4_are_frozen():
    import hashlib

    # Both were used by plan 3b-1b development runs and round 10, so they must never change;
    # follow-ups move to follow_up and pick (plan 3b-2 spec 5.8).
    route = (PROMPT_DIR / "route_v6.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(route).hexdigest() == (
        "7354e2bc93e9999996a644693fad67e7e15555459ab0dce89ec71d4ab74d96f4"
    )
    revise = (PROMPT_DIR / "revise_v4.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(revise).hexdigest() == (
        "cf2d42fd50b493cf7720b572b64657624c4f55965411236f6211b10c24112296"
    )


def test_follow_up_v1_and_pick_v1_are_frozen():
    import hashlib

    # Both were used by development run 1 of plan 3b-2 (one) Task 11, so they must never
    # change; the reader's next change is follow_up_v2.
    reader = (PROMPT_DIR / "follow_up_v1.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(reader).hexdigest() == (
        "40a4baf9a651feacbdb949df863d17a41f3df368adc0d5bb14805f5d3efd5ff4"
    )
    picker = (PROMPT_DIR / "pick_v1.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(picker).hexdigest() == (
        "303e3eb9f66d68e22096734f6679f40ec211b535374d498649a9c198490e02ff"
    )


def test_follow_up_v2_is_frozen():
    import hashlib

    # Used by development run 2 of plan 3b-2 (one) Task 11, so it must never change; the
    # reader's next change is follow_up_v3.
    reader = (PROMPT_DIR / "follow_up_v2.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(reader).hexdigest() == (
        "f100e08e5f98392df224e4429f1280f9ff47f0fbc868a125c9e8bf1ead7b9dbf"
    )


def test_follow_up_v3_and_pick_v2_are_frozen():
    import hashlib

    # Both were used by development run 3 of plan 3b-2 (one) Task 11, so they must never
    # change; the picker's next change is pick_v3.
    reader = (PROMPT_DIR / "follow_up_v3.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(reader).hexdigest() == (
        "30cc49a5984fcd99f9e16f851d8b29733edb15d2fc9168a88b7a50100a9e982d"
    )
    picker = (PROMPT_DIR / "pick_v2.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(picker).hexdigest() == (
        "965c927154ff948a0147bca40dc9e2357e7fb230ae912dfb952103ae3f303432"
    )


def test_pick_v3_is_frozen():
    import hashlib

    # Used by development run 4 of plan 3b-2 (one) Task 11, so it must never change; the
    # picker's next change is pick_v4. follow_up_v3 (run 4 too) is pinned above.
    picker = (PROMPT_DIR / "pick_v3.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(picker).hexdigest() == (
        "0d0f73bfceefc52eb3f090e8314873b6de8540b3d8a99532e5973ff8a0a0c116"
    )


def test_pick_v5_is_frozen():
    import hashlib

    # Used by the change-fixes development runs and round 12, so it must never change; the
    # picker's next change is pick_v6.
    picker = (PROMPT_DIR / "pick_v5.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(picker).hexdigest() == (
        "9075b47fbd3f0aed4b3c7228b7c17cbde69c1a83236a22c3742499940e5696db"
    )


def test_follow_up_v4_and_pick_v4_are_frozen():
    import hashlib

    # Used by development run 5 of plan 3b-2 (one) Task 11 and by round 11 (Task 12), so
    # they must never change; a change makes follow_up_v5 or pick_v5.
    reader = (PROMPT_DIR / "follow_up_v4.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(reader).hexdigest() == (
        "e6b676ed2bd19de9756b4dd8138a7abc2e4e7b2f31d2e1e903c704244709b274"
    )
    picker = (PROMPT_DIR / "pick_v4.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(picker).hexdigest() == (
        "cdc3b73e48cbb1d14481801638ea667114ab96b385d1786b737abfcc81d0d9e4"
    )


def test_pick_v4_is_pick_v3_with_the_answer_field_renamed():
    """Task 11 fix 4: DeepSeek cut answers right after the key "wish_met"; pick_v4 is pick_v3
    with every mention of that answer field named wish_result, and nothing else."""
    v3 = (PROMPT_DIR / "pick_v3.md").read_text(encoding="utf-8")
    v4 = (PROMPT_DIR / "pick_v4.md").read_text(encoding="utf-8")
    assert "wish_met" not in v4 and "wish_met" in v3
    assert v4 == v3.replace("wish_met", "wish_result")


def test_follow_up_v4_changes_two_principles_of_follow_up_v3():
    """Task 11 fix 4: a dish that breaks a rule or this meal's avoidance is dropped only when
    the message says not to replace it, and wish_targets lists only dishes the wish is about
    (a wish for something the plan lacks goes to add or remove). No new example."""
    v3 = (PROMPT_DIR / "follow_up_v3.md").read_text(encoding="utf-8")
    v4 = (PROMPT_DIR / "follow_up_v4.md").read_text(encoding="utf-8")
    old, new = v3.splitlines(), v4.splitlines()
    assert len(old) == len(new)
    changed = [(a, b) for a, b in zip(old, new, strict=True) if a != b]
    assert len(changed) == 2
    assert changed[0][0].startswith("- For each dish in remove, copy the words")
    assert changed[1][0].startswith("- wish is in the user's words. It guides")
    assert "list it with drop only when the message says not to replace it" in v4
    assert "(no other dish wanted, or fewer dishes)" in v4
    assert "or when it fits no dish of the plan" not in v4
    assert "wish_targets lists only dishes the wish is about" in v4
    assert "a wish for something the plan lacks goes to add or remove (replace) instead" in v4
    # The example that leaves out a rule-breaking dish keeps remove empty.
    assert '- "这顿不想吃排骨了": change; remove empty (code replaces the soup)' in v4
    assert v3.split("Examples", 1)[1] == v4.split("Examples", 1)[1]


def test_pick_v3_is_pick_v2_with_the_new_answer_fields():
    """Task 11 fix 3: pick_v3 is pick_v2 with its wording moved to the answer schema with no
    nullable field (recipe id 0 when nothing fits; wish_met "yes", "no" or "no_wish"); no
    line is added or removed and no null is left."""
    v2 = (PROMPT_DIR / "pick_v2.md").read_text(encoding="utf-8")
    v3 = (PROMPT_DIR / "pick_v3.md").read_text(encoding="utf-8")
    assert len(v3.splitlines()) == len(v2.splitlines())
    assert "null" not in v3 and "null" in v2
    assert "with one recipe id, or 0 when nothing fits" in v3
    assert 'wish_met is "yes" when the new dishes meet the wish, and "no_wish" when there' in v3
    changed = [(a, b) for a, b in zip(v2.splitlines(), v3.splitlines(), strict=True) if a != b]
    assert len(changed) == 8
    for old, _ in changed:
        assert "null" in old or "wish_met" in old


def test_follow_up_v3_and_pick_v2_add_one_principle_each():
    """Task 11 fix 2, items 4 and 5: each new version is its frozen predecessor plus its own
    lines, with no new example."""
    reader = (PROMPT_DIR / "follow_up_v3.md").read_text(encoding="utf-8")
    v2 = (PROMPT_DIR / "follow_up_v2.md").read_text(encoding="utf-8")
    assert "A dish in remove is never also in wish_targets" in reader
    assert "only that dish changes" in reader
    assert "by hand after it was proposed, a line after the plan says what" in reader
    assert "since the last reply" not in reader
    assert [line for line in v2.splitlines() if line not in reader.splitlines()] == [
        line for line in v2.splitlines() if line.startswith("You get the current plan")
    ]
    added = [line for line in reader.splitlines() if line not in v2.splitlines()]
    assert len(added) == 2 and added[1].startswith("- Apply each request of the message once.")
    picker = (PROMPT_DIR / "pick_v2.md").read_text(encoding="utf-8")
    v1 = (PROMPT_DIR / "pick_v1.md").read_text(encoding="utf-8")
    assert "When only a dish of another kind meets the wish, take that kind" in picker
    assert "The rules, the stated counts and an added slot's dish type never yield." in picker
    assert '"dishes" counts meat and vegetable dishes, never soups' in picker
    added = [line for line in picker.splitlines() if line not in v1.splitlines()]
    assert len(added) == 2
    for prompt in (reader, picker):
        examples = prompt.split("Examples", 1)[1].splitlines()[1:]
        assert len([line for line in examples if line.startswith("- ")]) <= 5


def test_follow_up_v2_states_the_task_11_principles():
    reader = (PROMPT_DIR / "follow_up_v2.md").read_text(encoding="utf-8")
    # Task 11 fix 1: a wish for something the plan lacks changes the plan, and a message that
    # changes who eats is people even when it asks for another change too.
    assert "A wish for something the plan lacks still changes the plan" in reader
    assert "A message that changes who eats is people, even when it also asks" in reader
    # Review fix 2: an add or a replace only for the wish is marked, and the example shows it.
    assert "Mark an add or a replace with for_wish when it is there only to meet the wish." in reader
    assert "add 1 vegetable dish, for_wish true." in reader
    # Everything else of follow_up_v1 is kept.
    v1 = (PROMPT_DIR / "follow_up_v1.md").read_text(encoding="utf-8")
    kept = [line for line in v1.splitlines() if line not in reader.splitlines()]
    assert kept == ['- "我先生以后都不吃羊肉" is people; "明天中午也帮我配一顿" is new_meal.']
    # Invented examples only, at most five.
    examples = reader.split("Examples, on a plan of", 1)[1].splitlines()[1:]
    assert len([line for line in examples if line.startswith("- ")]) <= 5


def test_follow_up_v4_and_pick_v4_load_and_stay_within_the_size_budget():
    # Plan 3b-2 change fixes Task 2: the picker in use is pick_v5 (checked below); plan 3b-2
    # (two): the reader in use is follow_up_v7. This test keeps the v4 numbers.
    reader = (PROMPT_DIR / "follow_up_v4.md").read_text(encoding="utf-8")
    picker = (PROMPT_DIR / "pick_v4.md").read_text(encoding="utf-8")
    assert len(reader) == 5499
    assert len(picker) == 4268
    revise_v4 = (PROMPT_DIR / "revise_v4.md").read_text(encoding="utf-8")
    replaced = len(load_prompt("route")) + len(revise_v4)
    # Spec 5.8a: the reader stays under half of route_v6 plus revise_v4; spec 6: the reader
    # and the picker together are shorter than the two prompts they replace.
    assert len(reader) < replaced / 2
    assert len(reader) + len(picker) < replaced
    # Spec 5.8: neither prompt lets internal words reach the user.
    assert 'Never use ids, the marks (LOCKED and the others) or "Diner N" in it' in reader
    assert 'never use ids, the plan\'s marks (LOCKED and the others) or "Diner N"' in picker
    # Spec 5.5: a taste wish guides the picker but is not a requirement.
    assert "The wish is not a requirement." in picker


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


def test_planner_v3_and_v4_are_frozen_and_planner_v5_is_current():
    import hashlib

    v3 = (PROMPT_DIR / "planner_v3.md").read_bytes().replace(b"\r\n", b"\n")
    # The committed planner_v3.md and planner_v4.md (versions records used) must never change.
    assert hashlib.sha256(v3).hexdigest() == (
        "123206bf2e1030911e6c7ecf80a0854d9336fc1c110e2c8715f6232b7c89fa1e"
    )
    v4 = (PROMPT_DIR / "planner_v4.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(v4).hexdigest() == (
        "8326569c8612e1c4b8b0a32f291fb0d976f261899ff86ce0f4d895b5b69b713b"
    )
    assert PROMPT_VERSIONS["planner"] == "planner_v5"
    v5 = load_prompt("planner")
    assert v5 == (PROMPT_DIR / "planner_v5.md").read_text(encoding="utf-8")
    # Plan 3b-1b spec 5.6: a taste wish the library cannot meet is answered, not searched on.
    assert "is a taste wish, not a requirement" in v5 and "wish_met to false" in v5
    assert "do not say in the note that the wish was not met" in v5
    assert "Do not count or list" in v5 and "one sentence" in v5


def _pick_v4_and_v5() -> tuple[str, str]:
    v4 = (PROMPT_DIR / "pick_v4.md").read_text(encoding="utf-8")
    v5 = (PROMPT_DIR / "pick_v5.md").read_text(encoding="utf-8")
    return v4, v5


def test_pick_v5_differs_from_pick_v4_only_in_the_wish_and_asked_add_principles():
    """Plan 3b-2 change fixes Task 2 (spec 5.3, 5.4): pick_v5 is pick_v4 with the wish_result
    principle reworded (it judges the dishes chosen), one new principle (an add the user asked
    for is filled whenever a usable dish exists), and examples in the new slot-line formats."""
    v4, v5 = _pick_v4_and_v5()
    head4 = v4.split("Examples:", 1)[0].splitlines()
    head5 = v5.split("Examples:", 1)[0].splitlines()
    wish_lines = [line for line in head4 if "wish_result is" in line]
    assert len(wish_lines) == 1
    missing = [line for line in head4 if line not in head5 and line not in wish_lines]
    assert missing == []
    assert wish_lines[0] not in head5
    # "yes" is about the dishes chosen.
    yes_lines = [line for line in head5 if '"yes"' in line]
    assert yes_lines and any(re.search(r"\bchos(e|en)\b", line) for line in yes_lines)
    # An add the user asked for is not left empty for the meal's balance.
    assert any("asked for" in line and "balance" in line for line in head5)
    # The examples use the picker's slot-line formats (picker._slot_line).
    examples = v5.split("Examples:", 1)[1]
    assert "which goes because of the user's words" in examples
    assert "in place of 12, the user's words" not in examples
    assert "Slot 0, in place of" not in examples


def test_pick_v5_size():
    v4, v5 = _pick_v4_and_v5()
    follow_up_v4 = (PROMPT_DIR / "follow_up_v4.md").read_text(encoding="utf-8")
    assert len(v5) <= len(v4) + 600
    # The 3b-2 design's bound: the reader and the picker together stay shorter than
    # route_v6 plus revise_v4 (12817 characters).
    assert len(follow_up_v4) + len(v5) < 12817
    assert 'never use ids, the plan\'s marks (LOCKED and the others) or "Diner N"' in v5


def _pick_v5_and_v6() -> tuple[str, str]:
    v5 = (PROMPT_DIR / "pick_v5.md").read_text(encoding="utf-8")
    v6 = (PROMPT_DIR / "pick_v6.md").read_text(encoding="utf-8")
    return v5, v6


def test_pick_v6_differs_from_pick_v5_in_four_points():
    """Plan 3b-2 change fixes Task 8: pick_v6 is pick_v5 with the asked-add principle for add
    slots only, a new principle that a replacement keeps the replaced dish's kind or answers
    0 (the dish stays), the shortlist's "main" field named, and examples in Task 7's slot-line
    formats."""
    v5, v6 = _pick_v5_and_v6()
    assert PROMPT_VERSIONS["pick"] == "pick_v6"
    assert load_prompt("pick") == v6
    head5 = v5.split("Examples:", 1)[0].splitlines()
    head6 = v6.split("Examples:", 1)[0].splitlines()
    # 1. The asked-add principle names add slots ("one more ... the user asked for").
    asked5 = [line for line in head5 if "asked for" in line and "balance" in line]
    asked6 = [line for line in head6 if "asked for" in line and "balance" in line]
    assert len(asked5) == 1 and len(asked6) == 1
    assert asked6[0] != asked5[0]
    assert "one more" in asked6[0] and "add" in asked6[0]
    # 2. A replacement keeps the replaced dish's kind; with none of that kind usable, 0.
    kind_lines = [line for line in head6 if "a soup for a soup" in line]
    assert len(kind_lines) == 1
    assert "kind" in kind_lines[0] and "0" in kind_lines[0] and "in place of" in kind_lines[0]
    assert "a staple for a staple" in kind_lines[0]
    assert "meat or vegetable dish for either" in kind_lines[0]
    assert "stays" in kind_lines[0]
    # An unfilled breaks_rule slot removes its dish (picker.apply_picks), so 0 keeps only
    # a dish that breaks no rule.
    assert "the dish stays unless it breaks a rule" in kind_lines[0]
    # 3. The input description names the shortlist's main field.
    description = next(line for line in head6 if line.startswith("You get a shortlist"))
    assert "main" in description
    assert "main" not in next(line for line in head5 if line.startswith("You get a shortlist"))
    # The two lines that let a wish change a replacement's kind, and that allowed 0 for no
    # other reason, are scoped to the new principle: the wish's yield is between meat and
    # vegetable dishes, and a slot in place of a dish is 0 when none of its kind fits.
    assert "When only a meat dish or only a vegetable dish meets the wish" in v6
    assert "When only a dish of another kind meets the wish, take that kind" not in v6
    assert "of any dish type" not in v6
    assert "only when no dish fits them (in place of a dish, none of its kind)" in v6
    # Every other line before the examples is kept.
    changed5 = [line for line in head5 if line not in head6]
    assert len(changed5) == 5
    assert asked5[0] in changed5
    added6 = [line for line in head6 if line not in head5]
    assert kind_lines[0] in added6 and asked6[0] in added6 and description in added6
    # 4. The examples follow picker._slot_line's formats.
    examples = v6.split("Examples:", 1)[1]
    assert "in place of 11 (" not in examples
    assert "or 0 when none is" in examples
    assert ": the library dish these words mean, by its title or by its main ingredients, or 0" in (
        examples
    )
    assert "which goes because of the user's words" in examples
    assert len([line for line in examples.splitlines() if line.startswith("- ")]) <= 5


def test_pick_v6_size():
    v5, v6 = _pick_v5_and_v6()
    follow_up_v4 = (PROMPT_DIR / "follow_up_v4.md").read_text(encoding="utf-8")
    assert len(v6) <= len(v5) + 400
    # The 3b-2 design's bound: the reader and the picker together stay shorter than
    # route_v6 plus revise_v4 (12817 characters).
    assert len(follow_up_v4) + len(v6) < 12817
    assert "never use ids, the plan's marks (LOCKED and the others) or \"Diner N\"" in v6
    assert "The wish is not a requirement." in v6


def test_prompt_versions_label():
    assert PROMPT_VERSION == "route_v6+parse_v8+planner_v5+follow_up_v7+pick_v6"
    assert PROMPT_VERSIONS["parse"] == "parse_v8"
    assert load_prompt("parse") == (PROMPT_DIR / "parse_v8.md").read_text(encoding="utf-8")


def test_revise_v1_v2_v3_are_frozen_and_revise_v4_keeps_its_rules():
    import hashlib

    v1 = (PROMPT_DIR / "revise_v1.md").read_bytes().replace(b"\r\n", b"\n")
    # revise_v1 and revise_v2 were used by plan 3b-1 runs, so they must never change.
    assert hashlib.sha256(v1).hexdigest() == (
        "9062ccb0da4dfd894023e79f6fabe118c5484f9dcc94c32ddc6563f55fbb4fc3"
    )
    v2 = (PROMPT_DIR / "revise_v2.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(v2).hexdigest() == (
        "90696404beb4ed7bfb54c36c666160d7462477cf8e916c2c1510609d7f1b5266"
    )
    # revise_v3 was used by development run 1 of plan 3b-1b, so it must never change.
    frozen = (PROMPT_DIR / "revise_v3.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(frozen).hexdigest() == (
        "2168b11130c1be2a936030552a7b9513548549951b1eb806c2493e1deed777d8"
    )
    # revise_v4, the last revise prompt, is no longer loaded (plan 3b-2); it stays as the
    # record of the runs that used it.
    assert "revise" not in PROMPT_VERSIONS
    v3 = (PROMPT_DIR / "revise_v4.md").read_text(encoding="utf-8")
    # Development run 1: a replaced dish is never dropped; a dish over the time limit is
    # never "not in the library".
    assert "A dish the words want replaced is never dropped" in v3
    assert "never drop the eggplant dish" in v3
    assert "over_time_limit" in v3 and "never say it is not in the library" in v3
    # Plan 3b-1b: drop or replace, the add-only kind, wishes for this meal only, the note.
    assert "mode drop" in v3 and "mode replace" in v3
    assert "- add: the message only adds dishes" in v3
    assert "LOCKED KEPT CONFLICT" in v3 and "replace_with" in v3
    # Task 5: the reviser's mark of a dish that breaks this meal's one-meal avoidance.
    from minemydinner.agent.change_text import BREAKS_MEAL_WISH

    assert v3.count(BREAKS_MEAL_WISH) == 3
    assert "not in the library and can be imported" in v3
    assert "\"锁定\"" in v3 and "so do not repeat that" in v3
    # No rule about counting dishes (plan 3b-1b spec 5.7, the user's decision).
    assert "count the dishes" not in v3
    # Development-set sentences stay out of the prompt (plan 3b-1b brief).
    assert "换个思路" not in v3 and "差点意思" not in v3


def test_route_v3_is_frozen_and_route_v4_sends_a_meal_wish_to_revise():
    import hashlib

    # route_v3 was used by development run 1 of plan 3b-1b, so it must never change.
    v3 = (PROMPT_DIR / "route_v3.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(v3).hexdigest() == (
        "154a7b399f6d8a509fbdd88921f071c83e7210ff27c22e73331cd221ae764438"
    )
    v4 = (PROMPT_DIR / "route_v4.md").read_text(encoding="utf-8")
    old = v3.decode("utf-8").splitlines()
    new = v4.splitlines()
    # One rule added, nothing else changed.
    added = [line for line in new if line not in old]
    assert len(added) == 1 and added[0].startswith("- A wish about this meal's taste")
    assert [line for line in new if line in old] == old
    assert "clearly asks for a different meal or a fresh start" in added[0]


def test_route_v4_is_frozen_and_route_v5_narrows_its_rule():
    import hashlib

    # route_v4 was used by development run 2 of plan 3b-1b, so it must never change.
    v4 = (PROMPT_DIR / "route_v4.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(v4).hexdigest() == (
        "1ee449adb21d6c81ff457abc73bce2a8a6ea160c2a417f6abdfbc2157acaf6a9"
    )
    v5 = (PROMPT_DIR / "route_v5.md").read_text(encoding="utf-8")
    old = v4.decode("utf-8").splitlines()
    new = v5.splitlines()
    # route_v4's own rule is replaced (narrowed), two rules are added; nothing else changed.
    assert [line for line in old if line not in new] == [
        line for line in old if line.startswith("- A wish about this meal's taste")
    ]
    added = [line for line in new if line not in old]
    assert len(added) == 3
    narrowed, reconsider, rule = added
    # Development run 2 (items 3, 4, 6): the rule needs a taste, an ingredient or a food;
    # a change word makes a named food a revision; a bare dish stays "unsure".
    assert "names a taste, an ingredient, or a kind of food" in narrowed
    assert "换换口味" in narrowed and "改成" in narrowed
    assert "clearly asks for a different meal or a fresh start" in narrowed
    assert "only says to reconsider or change" in reconsider and '"unsure"' in reconsider
    # Item 5: a rule about what someone eats, stated after a plan, is never "other".
    assert "never \"other\"" in rule and "lasting or for this meal only" in rule
    # The rules come before "other", and the rest keeps route_v4's order.
    assert new.index(rule) < next(i for i, l in enumerate(new) if l.startswith('- "other"'))
    assert [line for line in new if line in old] == [line for line in old if line in new]
    # Development-set sentences stay out of the prompt (plan 3b-1b brief).
    for sentence in ("琢磨", "折腾", "鱼或者虾", "海鲜", "虾", "清淡点"):
        assert sentence not in "\n".join(added)


def test_route_v5_is_frozen_and_route_v6_names_dishes_pointed_at():
    import hashlib

    # route_v5 was used by development run 3 of plan 3b-1b, so it must never change.
    v5 = (PROMPT_DIR / "route_v5.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(v5).hexdigest() == (
        "5c94492f9faf6251295371aaaa7ea3b421b18b67cd3bff7bc182d57f1f170e29"
    )
    assert PROMPT_VERSIONS["route"] == "route_v6"
    v6 = load_prompt("route")
    assert v6 == (PROMPT_DIR / "route_v6.md").read_text(encoding="utf-8")
    old = v5.decode("utf-8").splitlines()
    new = v6.splitlines()
    # One rule added, right after the "only says to reconsider" rule; nothing else changed.
    added = [line for line in new if line not in old]
    assert len(added) == 1
    (rule,) = added
    assert [line for line in new if line in old] == old
    reconsider = next(line for line in old if "only says to reconsider or change" in line)
    assert new.index(rule) == new.index(reconsider) + 1
    # Development run 3: words that point at the plan's dishes, with a word that removes or
    # swaps them, name those dishes ("revise"), also when every dish goes; a message that only
    # says to reconsider, with no such word on dishes, stays "unsure".
    assert '"这两道"' in rule and '"全都"' in rule and '"剩下的"' in rule
    assert '"both of them"' in rule
    assert "removes or swaps" in rule and '"revise"' in rule and '"unsure"' in rule
    assert '"全都不要了"' in rule
    # Development-set sentences stay out of the prompt (plan 3b-1b brief).
    for sentence in ("拿掉", "琢磨", "折腾", "这桌菜", "海鲜", "清淡", "再加一道", "全要辣的"):
        assert sentence not in rule


def test_parse_v4_is_frozen_and_parse_v5_changes_only_the_cold_dish_rule():
    import hashlib

    v4 =(PROMPT_DIR / "parse_v4.md").read_bytes().replace(b"\r\n", b"\n")
    # parse_v4 was used by evaluation round 8 (2026-11-18), so it must never change.
    assert hashlib.sha256(v4).hexdigest() == (
        "d5544c3033136c757d740f7818275c2ac732cbbb4c48bc8b89e5eb83fd33345f"
    )
    old = v4.decode("utf-8").splitlines()
    new = (PROMPT_DIR / "parse_v5.md").read_text(encoding="utf-8").splitlines()
    changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    assert len(old) == len(new) and len(changed) == 1
    assert "就做两个凉菜" in new[changed[0]] and "来个凉菜" in new[changed[0]]


def test_parse_v5_is_frozen_and_parse_v6_only_adds_lines():
    import hashlib

    v5 = (PROMPT_DIR / "parse_v5.md").read_bytes().replace(b"\r\n", b"\n")
    # parse_v5 was used by evaluation round 8 and plan 3b-1 runs, so it must never change.
    assert hashlib.sha256(v5).hexdigest() == (
        "8798894242eceaa040e5a8ff948767707d3c1f47741172eb8bc8ce30554cab82"
    )
    old = v5.decode("utf-8").splitlines()
    new = (PROMPT_DIR / "parse_v6.md").read_text(encoding="utf-8").splitlines()
    # Every parse_v5 line stays, unchanged and in order: parse_v6 only adds lines.
    rest = iter(new)
    assert all(any(line == other for other in rest) for line in old)
    added = "\n".join(line for line in new if line not in old)
    assert "[App note, not written by the user] Current plan:" in added
    assert "counts_stated_now" in added
    for example in ("再加一道吧", "4个菜就够了", "想要辣一些的"):
        assert example in added


def test_parse_v6_is_frozen_and_parse_v7_only_adds_lines():
    import hashlib

    v6 = (PROMPT_DIR / "parse_v6.md").read_bytes().replace(b"\r\n", b"\n")
    # parse_v6 was used by plan 3b-1 and 3b-1b runs, so it must never change.
    assert hashlib.sha256(v6).hexdigest() == (
        "a78a47933737b77133940290acc1c4b55e0ca5212bb44685e9f2958c2061dd00"
    )
    old = v6.decode("utf-8").splitlines()
    new = (PROMPT_DIR / "parse_v7.md").read_text(encoding="utf-8").splitlines()
    # Every parse_v6 line stays, unchanged and in order: parse_v7 only adds lines.
    rest = iter(new)
    assert all(any(line == other for other in rest) for line in old)
    added = "\n".join(line for line in new if line not in old)
    assert "meal_avoid" in added
    # Plan 3b-1b spec 5.4: one-meal wishes, taste words and remarks, and lasting rules.
    for example in (
        "今天不想吃肉",
        "不想吃土豆",
        "这顿不要辣的",
        "西红柿太多了",
        "我不吃肉",
        "我对花生过敏",
    ):
        assert example in added
    # Spec 5.1's boundary: "不想吃X" is a one-meal wish even without a time word; it holds
    # for this meal only (spec 5.4).
    assert "with or without a word about this meal" in added
    assert "until the user starts another meal" in added
    # Kept out on purpose: close to held-out sentences.
    assert "我对虾过敏" not in added


def test_parse_v7_is_frozen_and_parse_v8_only_adds_the_amount_rule():
    import hashlib

    v7 = (PROMPT_DIR / "parse_v7.md").read_bytes().replace(b"\r\n", b"\n")
    # parse_v7 was used by development runs 1 and 2 of plan 3b-1b, so it must never change.
    assert hashlib.sha256(v7).hexdigest() == (
        "364f75bf5f12212497ea77124b3cc14d614e01e237f18f1214e6dd0765db774f"
    )
    assert PROMPT_VERSIONS["parse"] == "parse_v8"
    old = v7.decode("utf-8").splitlines()
    new = load_prompt("parse").splitlines()
    # Every parse_v7 line stays, unchanged and in order: parse_v8 adds one rule and one
    # example.
    rest = iter(new)
    assert all(any(line == other for other in rest) for line in old)
    added = [line for line in new if line not in old]
    assert len(added) == 2
    rule, example = added
    # Development run 2 (items 1, 2; design 5.1): words about an amount are a remark about
    # the plan, never a one-meal avoidance; only leaving X out with no amount word is.
    assert rule.startswith("- Words about the amount of an ingredient or a kind of dish")
    assert "meal_avoid stays empty" in rule and "goes to meal_avoid" in rule
    for form in ("不要这么多 X", "X 太多了", "少放点 X", "fewer X dishes", "这顿别放 X"):
        assert form in rule
    # The rule sits with the other meal_avoid rules, before the examples.
    assert new.index(rule) == new.index(old[51]) + 1
    assert "豆腐用得太多了，少来点" in example and "meal_avoid empty" in example
    assert example == new[-1]
    # Kept out on purpose: the development set's own sentence.
    assert "鸡蛋" not in "\n".join(added)


def test_parse_v5_is_no_longer_than_parse_v3():
    v3 = (PROMPT_DIR / "parse_v3.md").read_text(encoding="utf-8").splitlines()
    v5 = (PROMPT_DIR / "parse_v5.md").read_text(encoding="utf-8").splitlines()
    assert len(v5) <= len(v3) == 79
    current = load_prompt("parse")
    # The current prompt may grow only a little past parse_v5 (parse_v7 adds the one-meal
    # versus lasting rule; parse_v8 the amount rule and its example).
    assert len(current.splitlines()) <= 102
    # A named removal is not a count change (spec 5.2).
    assert "汤就不要了" not in current and "那道汤不要了" in current
    for field in ("guests", "friend_rules", "self_rules", "diet_words"):
        assert field in current
    assert "三个菜，不要素菜" in current and REPLAN_MESSAGE["zh"] in current


def test_parsed_request_counts_stated_now():
    # Requests stored before the field still load, as "not known".
    assert ParsedRequest.model_validate({"dishes": 3}).counts_stated_now is None
    assert ParsedRequest(dishes=4, counts_stated_now=True).counts_stated_now is True
    schema = ParsedRequest.model_json_schema()["properties"]["counts_stated_now"]
    assert "newest" in schema["description"] and "relative" in schema["description"]
    # It never changes a new plan: the defaults and the ask-first question ignore it.
    for flag in (None, True, False):
        request = ParsedRequest(counts_stated_now=flag)
        assert apply_defaults(request) == apply_defaults(ParsedRequest())
        assert needs_question(request, "ask_first") == ["mode", "composition"]


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


def test_old_request_without_meal_avoid_loads():
    old = {"dishes": 2, "soups": 1, "mode": "shopping", "query": None, "must_use": []}
    request = ParsedRequest.model_validate(old)
    assert request.meal_avoid == []
    assert ParsedRequest(meal_avoid=["肉"]).meal_avoid == ["肉"]
    with pytest.raises(ValidationError):
        ParsedRequest(meal_avoid=["肉"] * 11)
    schema = ParsedRequest.model_json_schema()["properties"]["meal_avoid"]
    assert "this meal only" in schema["description"]
    # A one-meal avoidance never changes the spec or the people of a new plan: graph code
    # adds it to the meal's constraints as the meal's own rule.
    assert apply_defaults(ParsedRequest(meal_avoid=["肉"])) == apply_defaults(ParsedRequest())


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


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_fixed_messages_match_the_frontend_files(locale):
    chat = _chat_strings(locale)
    assert REPLAN_MESSAGE[locale] == chat["replanMessage"]
    assert ACTION_MESSAGES[locale] == chat["actionMessage"]


# ---- plan 3b-2 (two) Task 8: follow_up_v5 (spec 5.7) ----


def test_follow_up_v5_is_frozen():
    import hashlib

    # Used by the plan 3b-2 (two) development runs and round 14, so it must never change;
    # the reader's next change is follow_up_v6.
    reader = (PROMPT_DIR / "follow_up_v5.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(reader).hexdigest() == (
        "b743f267e514a580a2e2867ca028830627594353327bb1bade0896c8b69c73df"
    )
    assert len(reader.decode("utf-8")) == 6025


def test_follow_up_v5_states_the_diner_change_principles():
    v5 = (PROMPT_DIR / "follow_up_v5.md").read_text(encoding="utf-8")
    # 1. A people reading also carries the message's other changes.
    assert "also fill the other fields for the rest of the message" in v5
    # 2. A fresh start wins over people.
    assert "A fresh start wins" in v5
    # 3. A lasting rule about someone already eating is people.
    assert "a lasting rule about what someone eats, even someone who already eats" in v5
    # 4. any_mix and no_longer_must_use only when the message says so.
    assert "any_mix" in v5 and "no_longer_must_use" in v5
    # Task 8 fix round 1: meal_avoid is the whole list for a people reading too.
    assert "meal_avoid is the whole list after this message, whatever the kind (people too)" in v5
    # The input names the confirmed set.
    assert "who eats this meal" in v5
    # Kept from v4: internal words never reach the user.
    assert 'Never use ids, the marks (LOCKED and the others) or "Diner N" in it' in v5
    # Invented examples only, at most five lines.
    examples = v5.split("Examples, on a plan of", 1)[1].splitlines()[1:]
    assert len([line for line in examples if line.startswith("- ")]) <= 5


# ---- plan 3b-2 (two): follow_up_v6, after round 14 ----


def _follow_up_v5_and_v6() -> tuple[str, str]:
    v5 = (PROMPT_DIR / "follow_up_v5.md").read_text(encoding="utf-8")
    v6 = (PROMPT_DIR / "follow_up_v6.md").read_text(encoding="utf-8")
    return v5, v6


def test_follow_up_v6_is_frozen():
    import hashlib

    # Used by three evaluation runs after round 14, so it must never change; the reader's
    # next change is follow_up_v7.
    reader = (PROMPT_DIR / "follow_up_v6.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(reader).hexdigest() == (
        "5d6ce96cca5e5d7ed6862be91bb389a062489c748e91d198d0e176766bf12770"
    )
    assert len(reader.decode("utf-8")) == 6407


def test_follow_up_v6_is_within_budget():
    _, v6 = _follow_up_v5_and_v6()
    assert len(v6) == 6407
    revise_v4 = (PROMPT_DIR / "revise_v4.md").read_text(encoding="utf-8")
    route_v6 = (PROMPT_DIR / "route_v6.md").read_text(encoding="utf-8")
    # Spec 3: under half of route_v6 plus revise_v4 (12817 characters).
    assert len(v6) < (len(route_v6) + len(revise_v4)) / 2
    # The reader and the picker in use stay shorter than the two prompts they replace.
    assert len(v6) + len(load_prompt("pick")) < len(route_v6) + len(revise_v4)


def test_follow_up_v6_sharpens_three_lines_of_follow_up_v5():
    """Round 14: a one-meal avoidance of a food removes no dish (code replaces the dishes
    that break it), words about an amount are a wish with targets (under code's cap), and an
    add named by a food keeps the words. v6 sharpens v5's own lines and one example."""
    v5, v6 = _follow_up_v5_and_v6()
    old, new = v5.splitlines(), v6.splitlines()
    assert len(old) == len(new)
    changed = [(a, b) for a, b in zip(old, new, strict=True) if a != b]
    assert [a[:30] for a, _ in changed] == [
        "You get the current plan (one ",
        "- For each dish in remove, cop",
        "- A dish the user names, as a ",
        "- wish is in the user's words.",
        '- "这顿不想吃排骨了": change; remove e',
    ]
    # 1. Only words that point at a dish itself put it in remove.
    assert "only when the message points at that dish itself" in v6
    assert "words that only leave a food out of this meal point at no dish" in v6
    # 2. An amount is a wish with wish_targets, never plain removals.
    assert "Too much of something is a wish too" not in v6
    assert "are a wish with wish_targets, never left out and never plain removals" in v6
    # 3. An add named by a food keeps the words.
    assert "only names a food or a kind of food" in v6
    assert "an add keeps those words as its name, or as the wish with for_wish" in v6
    # Kept: internal words never reach the user; at most five example lines.
    assert 'Never use ids, the marks (LOCKED and the others) or "Diner N" in it' in v6
    examples = v6.split("Examples, on a plan of", 1)[1].splitlines()[1:]
    assert len([line for line in examples if line.startswith("- ")]) <= 5


# ---- plan 3b-2 (two): follow_up_v7, after three runs of follow_up_v6 ----


def _follow_up_v6_and_v7() -> tuple[str, str]:
    v6 = (PROMPT_DIR / "follow_up_v6.md").read_text(encoding="utf-8")
    v7 = (PROMPT_DIR / "follow_up_v7.md").read_text(encoding="utf-8")
    return v6, v7


def test_follow_up_v7_is_in_use():
    assert PROMPT_VERSIONS["follow_up"] == "follow_up_v7"
    assert load_prompt("follow_up") == _follow_up_v6_and_v7()[1]


def test_follow_up_v7_is_within_budget():
    _, v7 = _follow_up_v6_and_v7()
    assert len(v7) == 6398
    revise_v4 = (PROMPT_DIR / "revise_v4.md").read_text(encoding="utf-8")
    route_v6 = (PROMPT_DIR / "route_v6.md").read_text(encoding="utf-8")
    # Spec 3: under half of route_v6 plus revise_v4 (12817 characters).
    assert len(v7) < (len(route_v6) + len(revise_v4)) / 2
    # The reader and the picker in use stay shorter than the two prompts they replace.
    assert len(v7) + len(load_prompt("pick")) < len(route_v6) + len(revise_v4)


def test_follow_up_v7_states_three_principles_once():
    """Three runs of v6: a one-meal avoidance still removed its dish with drop, an allergy
    of someone eating was read as meal_avoid, and the reader rewrote the user's words
    (translated a wish, swapped a food the plan lacks, stated a count). v7 sharpens v6's own
    lines and adds one line that keeps the user's words."""
    v6, v7 = _follow_up_v6_and_v7()
    # 1. drop is only for fewer dishes; a one-meal avoidance removes nothing.
    assert "Use drop when the words only say not to have it" not in v7
    assert "point at no dish" not in v7
    assert "Use drop only when the words ask for fewer dishes" in v7
    assert "goes to meal_avoid only (kind change); remove stays empty" in v7
    # The avoidance rule is stated once, at the plan description.
    assert v7.count("replaces dishes that break a rule") == 1
    assert "is replaced by code" not in v7
    # 2. A rule about a particular person is people, whatever its duration.
    assert "a rule about what a particular person eats (an allergy" in v7
    assert "for no one in particular" in v7
    assert "beyond this meal" not in v7
    # 3. The user's words are kept, never translated or corrected; counts only when stated.
    assert "never translated or corrected" in v7
    assert "never as something it has" in v7
    assert "never a total it does not state" in v7
    # Only words that change a dish or food the plan lacks are ambiguous for that reason;
    # an avoidance, a rule or a wish for something the plan lacks never is.
    assert "Words that change a dish or food the plan lacks" in v7
    assert "This never makes an avoidance, a rule or a wish for something the plan lacks" in v7
    assert "Words about something the plan does not have" not in v7
    assert "(its own line below)" in v7 and "- A wish for something the plan lacks" in v7
    assert "quote the words of the latest message that point at it, copied exactly" in v7
    # Kept: internal words never reach the user; the examples are v6's, at most five lines.
    assert 'Never use ids, the marks (LOCKED and the others) or "Diner N" in it' in v7
    assert v7.split("Examples, on a plan of", 1)[1] == v6.split("Examples, on a plan of", 1)[1]
    examples = v7.split("Examples, on a plan of", 1)[1].splitlines()[1:]
    assert len([line for line in examples if line.startswith("- ")]) <= 5


def test_follow_up_reading_schema_matches_follow_up_v7():
    """The schema descriptions reach the reader with the prompt (`with_structured_output`),
    so they must not contradict it: drop is only for fewer dishes, a rule about a particular
    person is people, and the wish keeps the user's words and language."""
    from minemydinner.agent.follow_up import FollowUpReading, ReadRemoval

    mode = ReadRemoval.model_fields["mode"].description
    assert "only say not to have it" not in mode
    assert "ask for fewer dishes, with no other dish wanted in its place" in mode
    assert "character for character" in ReadRemoval.model_fields["quote"].description
    fields = FollowUpReading.model_fields
    assert "a rule about what a particular person eats" in fields["kind"].description
    assert "never translated or corrected" in fields["wish"].description
    assert "for no one in particular" in fields["meal_avoid"].description
