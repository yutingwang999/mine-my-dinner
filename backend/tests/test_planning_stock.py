from minemydinner.models import Diner, RecipeIngredient
from minemydinner.planning.context import MealConstraints, RuleRef, diner_codes
from minemydinner.planning.stock import compute_missing, is_available, missing_cookware
from tests.planningfakes import (
    always_in_stock,
    L,
    ctx_for,
    make_recipe,
    not_always_in_stock,
    own_cookware,
    rule,
    self_diner,
    stock,
)
from tests.recipefakes import by_zh, seeded  # noqa: F401


def missing(session, recipe):
    return compute_missing(session, ctx_for(session), [recipe.id])[recipe.id]


def test_parent_need_met_by_child_in_stock(session, seeded):
    not_always_in_stock(session, "酱油")
    r = make_recipe(session, "红烧", lines=[L("酱油", role="seasoning")])
    stock(session, "生抽")
    assert missing(session, r) == []


def test_parent_need_unmet_without_child(session, seeded):
    not_always_in_stock(session, "酱油")
    r = make_recipe(session, "红烧", lines=[L("酱油", role="seasoning")])
    assert [m.reason for m in missing(session, r)] == ["not_in_stock"]


def test_meat_parent_met_by_child(session, seeded):
    r = make_recipe(session, "炖", lines=[L("猪肉", role="main")])
    stock(session, "五花肉", level="plenty")
    assert missing(session, r) == []


def test_child_need_not_met_by_parent(session, seeded):
    r = make_recipe(session, "凉拌", lines=[L("生抽", role="seasoning")])
    stock(session, "酱油")
    assert [m.name for m in missing(session, r)] == ["生抽"]


def test_child_need_not_met_by_always_in_stock_parent(session, seeded):
    r = make_recipe(session, "凉拌", lines=[L("生抽", role="seasoning")])
    assert [m.name for m in missing(session, r)] == ["生抽"]


def test_level_none_counts_as_missing(session, seeded):
    r = make_recipe(session, "炒肉", lines=[L("五花肉", role="main")])
    stock(session, "五花肉", level="none")
    assert [(m.name, m.reason) for m in missing(session, r)] == [("五花肉", "level_none")]


def test_child_level_none_does_not_satisfy_parent(session, seeded):
    r = make_recipe(session, "炖", lines=[L("猪肉", role="main")])
    stock(session, "五花肉", level="none")
    assert len(missing(session, r)) == 1


def test_zero_count_counts_as_missing(session, seeded):
    r = make_recipe(session, "炒", lines=[L("鸡胸肉", role="main")])
    stock(session, "鸡胸肉", count=0)
    assert [m.reason for m in missing(session, r)] == ["not_in_stock"]


def test_positive_count_is_available(session, seeded):
    r = make_recipe(session, "炒", lines=[L("鸡胸肉", role="main")])
    stock(session, "鸡胸肉", count=2)
    assert missing(session, r) == []


def test_always_in_stock_needs_no_inventory_row(session, seeded):
    r = make_recipe(session, "拌", lines=[L("盐", role="seasoning")])
    assert missing(session, r) == []


def test_optional_line_is_ignored(session, seeded):
    r = make_recipe(session, "拌", lines=[L("五花肉", optional=True), L("盐")])
    assert missing(session, r) == []


def test_unlinked_line_is_unrecognized(session, seeded):
    r = make_recipe(session, "拌")
    r.ingredients.append(
        RecipeIngredient(
            raw="秘制酱", name="秘制酱", ingredient_id=None, amount_type="missing", role="seasoning"
        )
    )
    session.flush()
    result = missing(session, r)
    assert [(m.name, m.ingredient_id, m.reason) for m in result] == [("秘制酱", None, "unrecognized")]


def test_choice_group_satisfied_by_one_member_via_child(session, seeded):
    not_always_in_stock(session, "酱油")
    r = make_recipe(session, "吐司", lines=[L("酸奶", choice_group=1), L("酱油", choice_group=1)])
    stock(session, "生抽")
    assert missing(session, r) == []


def test_unsatisfied_choice_group_reports_every_member(session, seeded):
    not_always_in_stock(session, "酱油")
    r = make_recipe(
        session,
        "吐司",
        lines=[
            L("酸奶", choice_group=1),
            L("酱油", choice_group=1),
            L("五花肉", choice_group=1, optional=True),
        ],
    )
    result = missing(session, r)
    assert sorted(m.name for m in result) == ["酱油", "酸奶"]
    assert {m.choice_group for m in result} == {1}


def test_seasoning_checks_availability_only(session, seeded):
    r = make_recipe(session, "拌", lines=[L("生抽", role="seasoning")])
    stock(session, "生抽", count=0)
    assert missing(session, r) == []


def test_every_requested_id_has_a_key(session, seeded):
    r = make_recipe(session, "空")
    assert compute_missing(session, ctx_for(session), [r.id, 999999]) == {r.id: [], 999999: []}


def test_is_available(session, seeded):
    ctx = ctx_for(session)
    assert is_available(ctx, by_zh(session, "盐").id, "seasoning")
    assert not is_available(ctx, by_zh(session, "五花肉").id, "main")


def test_missing_cookware_compares_by_id(session, seeded):
    r = make_recipe(session, "烤", cookware=["烤箱"])
    own_cookware(session, "炒锅")
    names = [name for _, name in missing_cookware(session, ctx_for(session), [r.id])[r.id]]
    assert names == ["烤箱"]


def test_owned_cookware_is_not_missing(session, seeded):
    r = make_recipe(session, "烤", cookware=["烤箱"])
    own_cookware(session, "烤箱")
    assert missing_cookware(session, ctx_for(session), [r.id]) == {r.id: []}


def test_context_loads_rules_for_selected_diners_and_guests(session, seeded):
    me = self_diner(session)
    other = Diner(display_name="Other")
    session.add(other)
    session.flush()
    rule(session, me.id, "allergy", allergen="milk")
    rule(session, other.id, "dislike", ingredient="酸奶")
    guest = RuleRef("guest:1", "allergy", "fish", None, None)
    ctx = ctx_for(session, me.id, guest_rules=(guest,))
    assert ctx.rules == (RuleRef(f"diner:{me.id}", "allergy", "milk", None, None), guest)
    assert by_zh(session, "酱油").id in ctx.always_in_stock
    assert ctx.parents[by_zh(session, "生抽").id] == by_zh(session, "酱油").id
    assert ctx.stock_modes[by_zh(session, "鸡胸肉").id] == "count"


def test_diner_codes_follow_order_and_guests_continue():
    c = MealConstraints(
        diner_ids=(7, 3),
        guest_rules=(
            RuleRef("guest:1", "allergy", "fish", None, None),
            RuleRef("guest:1", "dislike", None, None, "cilantro"),
            RuleRef("guest:2", "dislike", None, None, "cilantro"),
        ),
    )
    assert diner_codes(c) == {
        "diner:7": "Diner 1",
        "diner:3": "Diner 2",
        "guest:1": "Diner 3",
        "guest:2": "Diner 4",
    }


def test_parent_need_met_by_always_in_stock_child(session, seeded):
    not_always_in_stock(session, "酱油")
    always_in_stock(session, "生抽")
    r = make_recipe(session, "红烧", lines=[L("酱油", role="seasoning")])
    assert missing(session, r) == []


def test_choice_group_zero_is_a_real_group(session, seeded):
    not_always_in_stock(session, "酱油")
    r = make_recipe(session, "吐司", lines=[L("酸奶", choice_group=0), L("盐", choice_group=0)])
    assert missing(session, r) == []
    r2 = make_recipe(session, "吐司2", lines=[L("酸奶", choice_group=0), L("酱油", choice_group=0)])
    assert sorted(m.name for m in missing(session, r2)) == ["酱油", "酸奶"]
