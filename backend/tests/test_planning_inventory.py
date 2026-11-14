from datetime import date

from minemydinner.models import Inventory
from minemydinner.planning.context import RuleRef
from minemydinner.planning.inventory import (
    InventoryChange,
    inventory_snapshot,
    meal_preferences,
    update_inventory,
)
from tests.planningfakes import ctx_for, own_cookware, rule, self_diner, stock
from tests.recipefakes import by_zh, seeded  # noqa: F401


def test_update_inventory_rejects_unknown_names(session, seeded):
    out = update_inventory(session, [InventoryChange(name="龙肝", action="set", count=1)])
    assert out["unmatched"] == ["龙肝"] and out["applied"] == []


def test_update_inventory_sets_and_removes(session, seeded):
    scallion = by_zh(session, "葱")
    out = update_inventory(
        session,
        [
            InventoryChange(
                name="葱", action="set", level="low", grams=300.0, expires_on=date(2030, 1, 1)
            )
        ],
    )
    assert out["applied"] == ["葱"]
    row = session.get(Inventory, scallion.id)
    assert (row.level, row.grams, row.expires_on) == ("low", 300.0, date(2030, 1, 1))
    out = update_inventory(session, [InventoryChange(name="葱", action="remove")])
    assert out["applied"] == ["葱"] and session.get(Inventory, scallion.id) is None


def test_update_inventory_resolves_aliases(session, seeded):
    out = update_inventory(session, [InventoryChange(name="potato", action="set", count=2)])
    assert out["applied"] == ["potato"] and out["unmatched"] == []


def test_update_inventory_rejects_values_that_do_not_fit_stock_mode(session, seeded):
    out = update_inventory(session, [InventoryChange(name="土豆", action="set", level="low")])
    assert out["applied"] == [] and out["unmatched"] == []
    assert out["rejected"] == ["土豆"]
    assert session.get(Inventory, by_zh(session, "土豆").id) is None


def test_snapshot_lists_stock_always_in_stock_and_cookware(session, seeded):
    stock(session, "葱", level="low", grams=200.0, expires_on=date(2030, 1, 2))
    stock(session, "土豆", count=3, expires_on=date(2030, 1, 1))
    own_cookware(session, "炒锅")
    snap = inventory_snapshot(session)
    assert {i["name_zh"] for i in snap["ingredients"]} == {"土豆", "葱"}
    scallion = next(i for i in snap["ingredients"] if i["name_zh"] == "葱")
    assert scallion["level"] == "low" and scallion["grams"] == 200.0
    assert set(scallion) == {
        "id", "name_zh", "name_en", "category", "stock_mode", "count",
        "level", "grams", "expires_on",
    }
    assert "酱油" in snap["always_in_stock"]
    assert snap["cookware"] == ["炒锅"]
    by_expiry = inventory_snapshot(session, sort_by_expiry=True)
    assert [i["name_zh"] for i in by_expiry["ingredients"]] == ["土豆", "葱"]


def test_snapshot_filters_by_category(session, seeded):
    stock(session, "土豆", count=1)
    stock(session, "猪肉", level="low")
    category = by_zh(session, "土豆").category
    assert category != by_zh(session, "猪肉").category
    snap = inventory_snapshot(session, category=category)
    assert [i["name_zh"] for i in snap["ingredients"]] == ["土豆"]


def test_meal_preferences_use_codes_not_names(session, seeded):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    assert meal_preferences(session, ctx_for(session, self_diner(session).id)) == [
        "Diner 1: allergy egg"
    ]


def test_meal_preferences_cover_rule_kinds_guests_and_unconfirmed(session, seeded):
    me = self_diner(session).id
    rule(session, me, "dietary restriction", ingredient="香菜")
    rule(session, me, "dislike", ingredient="芹菜")
    rule(session, me, "dietary restriction", text="no spice")
    guest = RuleRef("guest:1", "allergy", "peanut", None, None)
    ctx = ctx_for(session, me, guest_rules=(guest,), unconfirmed_diners=1)
    lines = meal_preferences(session, ctx)
    assert lines == [
        "Diner 1: does not eat 香菜",
        "Diner 1: dislikes 芹菜",
        "Diner 1: does not eat no spice",
        "Diner 2: allergy peanut",
        "Diner 3: dietary limits unconfirmed",
    ]
    assert "Me" not in " ".join(lines)


def test_meal_preferences_ignore_unselected_diners(session, seeded):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    assert meal_preferences(session, ctx_for(session)) == []


def test_bad_values_are_rejected_and_the_rest_still_apply(session, seeded):
    out = update_inventory(
        session,
        [
            InventoryChange(name="葱", action="set", level="some"),
            InventoryChange(name="土豆", action="set", count=-1),
            InventoryChange(name="姜", action="set", level="low", grams=-5.0),
            InventoryChange(name="蒜", action="set", level="plenty"),
        ],
    )
    assert out["rejected"] == ["葱", "土豆", "姜"]
    assert out["applied"] == ["蒜"]
    assert session.get(Inventory, by_zh(session, "蒜").id).level == "plenty"
