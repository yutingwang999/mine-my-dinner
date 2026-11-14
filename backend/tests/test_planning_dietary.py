from minemydinner.models import Ingredient
from minemydinner.planning.dietary import check_recipes
from tests.planningfakes import L, make_recipe, rule_ref
from tests.recipefakes import by_zh, seeded  # noqa: F401


def allergy(allergen, key="diner:1"):
    return rule_ref("allergy", allergen=allergen, diner_key=key)


def restriction(session, name):
    return rule_ref("dietary restriction", ingredient_id=by_zh(session, name).id)


def dislike(session, name):
    return rule_ref("dislike", ingredient_id=by_zh(session, name).id)


def test_allergen_rule_hits_linked_line(session, seeded):
    r = make_recipe(session, "蛋", lines=[L("鸡蛋")])
    check = check_recipes(session, [allergy("egg")], [r.id])
    assert [(c.recipe_id, c.line_name, c.allergen) for c in check.hard] == [(r.id, "鸡蛋", "egg")]
    assert check.any_allergy


def test_allergen_rule_hits_child_via_parent_allergens(session, seeded):
    r = make_recipe(session, "红烧", lines=[L("生抽", role="seasoning")])
    check = check_recipes(session, [allergy("soy")], [r.id])
    assert [c.line_name for c in check.hard] == ["生抽"]


def test_allergen_rule_ignores_safe_recipe(session, seeded):
    r = make_recipe(session, "炒肉", lines=[L("五花肉")])
    check = check_recipes(session, [allergy("peanut")], [r.id])
    assert check.hard == []
    assert not check.blocked_by_unknown(r.id)


def test_rule_on_parent_ingredient_covers_child_line(session, seeded):
    r = make_recipe(session, "炒肉", lines=[L("五花肉")])
    check = check_recipes(session, [restriction(session, "猪肉")], [r.id])
    assert [(c.line_name, c.ingredient_id) for c in check.hard] == [
        ("五花肉", by_zh(session, "猪肉").id)
    ]


def test_rule_on_child_does_not_cover_parent_line(session, seeded):
    r = make_recipe(session, "炖", lines=[L("猪肉")])
    assert check_recipes(session, [restriction(session, "五花肉")], [r.id]).hard == []


def test_ingredient_rule_matches_unlinked_line_by_alias(session, seeded):
    r = make_recipe(session, "汤", lines=[L("葱花", linked=False)])
    check = check_recipes(session, [dislike(session, "葱")], [r.id])
    assert [c.line_name for c in check.dislikes] == ["葱花"]


def test_ingredient_rule_on_parent_matches_unlinked_child_wording(session, seeded):
    r = make_recipe(session, "汤", lines=[L("猪里脊", linked=False)])
    check = check_recipes(session, [restriction(session, "猪肉")], [r.id])
    assert [c.line_name for c in check.hard] == ["猪里脊"]


def test_unlinked_match_is_casefolded_and_trimmed(session, seeded):
    r = make_recipe(session, "汤", lines=[L("  Scallions ", linked=False)])
    assert len(check_recipes(session, [dislike(session, "葱")], [r.id]).dislikes) == 1


def test_ingredient_rule_matches_personal_alias(session, seeded):
    by_zh(session, "葱").user_aliases_zh = ["小香葱末"]
    session.flush()
    r = make_recipe(session, "汤", lines=[L("小香葱末", linked=False)])
    assert len(check_recipes(session, [dislike(session, "葱")], [r.id]).dislikes) == 1


def test_text_rule_matches_raw_wording(session, seeded):
    r = make_recipe(session, "汤", lines=[L("葱花", linked=False)])
    r.ingredients[0].raw = "少许香菜末"
    session.flush()
    check = check_recipes(session, [rule_ref("dislike", text="香菜")], [r.id])
    assert [c.text for c in check.dislikes] == ["香菜"]


def test_text_rule_is_casefolded_and_trimmed(session, seeded):
    r = make_recipe(session, "汤", lines=[L("葱花", linked=False)])
    r.ingredients[0].raw = "Coriander leaves"
    session.flush()
    rules = [rule_ref("dislike", text="  CORIANDER ")]
    assert len(check_recipes(session, rules, [r.id]).dislikes) == 1


def test_text_rule_matches_ingredient_alias_of_linked_line(session, seeded):
    r = make_recipe(session, "拌", lines=[L("香菜")])
    assert len(check_recipes(session, [rule_ref("dislike", text="芫荽")], [r.id]).dislikes) == 1


def test_text_rule_matches_parent_terms_of_linked_line(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    rules = [rule_ref("dietary restriction", text="猪肉丝")]
    assert len(check_recipes(session, rules, [r.id]).hard) == 1


def test_empty_text_rule_never_matches(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    rules = [rule_ref("dislike", text=""), rule_ref("dislike", text="   "), rule_ref("dislike")]
    assert check_recipes(session, rules, [r.id]).dislikes == []


def test_restriction_is_hard_dislike_is_soft(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉"), L("芹菜")])
    check = check_recipes(session, [restriction(session, "猪肉"), dislike(session, "芹菜")], [r.id])
    assert [c.rule_type for c in check.hard] == ["dietary restriction"]
    assert [c.rule_type for c in check.dislikes] == ["dislike"]
    assert not check.any_allergy


def test_optional_and_choice_group_lines_are_checked(session, seeded):
    r = make_recipe(
        session,
        "面",
        lines=[L("花生", optional=True), L("鸡蛋", choice_group=1), L("香菜", choice_group=1)],
    )
    check = check_recipes(session, [allergy("peanut"), allergy("egg")], [r.id])
    assert sorted(c.line_name for c in check.hard) == ["花生", "鸡蛋"]


def test_diner_key_is_reported_per_rule(session, seeded):
    r = make_recipe(session, "蛋", lines=[L("鸡蛋")])
    check = check_recipes(session, [allergy("egg", "guest:1")], [r.id])
    assert [c.diner_key for c in check.hard] == ["guest:1"]


def test_unknown_allergens_block_only_when_someone_has_an_allergy(session, seeded):
    r = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    assert check_recipes(session, [allergy("egg")], [r.id]).blocked_by_unknown(r.id)
    only_restriction = check_recipes(session, [restriction(session, "猪肉")], [r.id])
    assert not only_restriction.blocked_by_unknown(r.id)


def test_unlinked_line_is_unknown_and_unrecognized(session, seeded):
    r = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False), L("鸡蛋")])
    check = check_recipes(session, [allergy("peanut")], [r.id])
    assert check.unknown_allergen_lines == {r.id: ["某种酱"]}
    assert check.unrecognized_lines == {r.id: ["某种酱"]}


def test_null_allergens_user_entry_is_unknown(session, seeded):
    entry = Ingredient(
        name_zh="自制酱",
        category="seasoning",
        stock_mode="availability",
        allergens=None,
        source="user",
    )
    session.add(entry)
    session.flush()
    r = make_recipe(session, "拌", lines=[L("自制酱")])
    check = check_recipes(session, [allergy("egg")], [r.id])
    assert check.unknown_allergen_lines == {r.id: ["自制酱"]}
    assert check.unrecognized_lines == {}
    assert check.blocked_by_unknown(r.id)


def test_no_rules_still_reports_unknown_and_unrecognized(session, seeded):
    r = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    check = check_recipes(session, [], [r.id])
    assert check.hard == [] and check.dislikes == []
    assert check.unknown_allergen_lines == {r.id: ["某种酱"]}
    assert check.unrecognized_lines == {r.id: ["某种酱"]}
    assert not check.any_allergy
    assert not check.blocked_by_unknown(r.id)


def test_several_recipes_are_checked_separately(session, seeded):
    a = make_recipe(session, "蛋", lines=[L("鸡蛋")])
    b = make_recipe(session, "肉", lines=[L("五花肉")])
    check = check_recipes(session, [allergy("egg")], [a.id, b.id])
    assert {c.recipe_id for c in check.hard} == {a.id}
    assert check_recipes(session, [allergy("egg")], []).hard == []


def test_ingredient_rule_matches_unlinked_line_containing_wording(session, seeded):
    r = make_recipe(session, "汤", lines=[L("猪肉末", linked=False)])
    assert len(check_recipes(session, [restriction(session, "猪肉")], [r.id]).hard) == 1


def test_ingredient_rule_matches_unlinked_line_by_raw_wording(session, seeded):
    r = make_recipe(session, "汤", lines=[L("肉末", linked=False)])
    r.ingredients[0].raw = "猪肉末 100克"
    session.flush()
    assert len(check_recipes(session, [restriction(session, "猪肉")], [r.id]).hard) == 1


def test_child_rule_does_not_match_unlinked_line_with_parent_wording(session, seeded):
    r = make_recipe(session, "汤", lines=[L("猪肉", linked=False)])
    assert check_recipes(session, [restriction(session, "五花肉")], [r.id]).hard == []


def test_text_rule_is_contained_in_linked_ingredient_terms(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    assert "猪" not in r.ingredients[0].name + r.ingredients[0].raw
    assert len(check_recipes(session, [rule_ref("dietary restriction", text="猪")], [r.id]).hard) == 1


def test_known_child_allergen_with_null_parent_is_hard_and_unknown(session, seeded):
    parent = Ingredient(
        name_zh="自制酱类", category="seasoning", stock_mode="availability",
        allergens=None, source="user",
    )
    session.add(parent)
    session.flush()
    child = Ingredient(
        name_zh="自制蛋酱", category="seasoning", stock_mode="availability",
        allergens=["egg"], source="user", parent_id=parent.id,
    )
    session.add(child)
    session.flush()
    r = make_recipe(session, "拌", lines=[L("自制蛋酱")])
    check = check_recipes(session, [allergy("egg")], [r.id])
    assert [c.line_name for c in check.hard] == ["自制蛋酱"]
    assert check.unknown_allergen_lines == {r.id: ["自制蛋酱"]}


def test_unknown_rule_type_is_hard(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    check = check_recipes(session, [rule_ref("mystery", ingredient_id=by_zh(session, "五花肉").id)], [r.id])
    assert len(check.hard) == 1 and check.dislikes == []


def test_bad_allergen_raises(session, seeded):
    import pytest

    r = make_recipe(session, "炒", lines=[L("五花肉")])
    with pytest.raises(ValueError):
        check_recipes(session, [allergy("pollen")], [r.id])
