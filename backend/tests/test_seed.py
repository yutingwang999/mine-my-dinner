import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from minemydinner.constants import ALLERGENS, CATEGORIES, STOCK_MODES
from minemydinner.models import AlwaysInStock, Cookware, Diner, Ingredient
from minemydinner.seed import SEED_DIR, seed_all


@pytest.fixture(scope="module")
def ingredients_json():
    return json.loads((SEED_DIR / "ingredients.json").read_text(encoding="utf-8"))


@pytest.fixture
def seeded(session):
    seed_all(session)
    session.flush()
    return session


def by_zh(session, name):
    return session.scalars(select(Ingredient).where(Ingredient.name_zh == name)).one()


def always_in_stock_names(session):
    query = select(Ingredient.name_zh).join(
        AlwaysInStock, AlwaysInStock.ingredient_id == Ingredient.id
    )
    return set(session.scalars(query))


def terms_of(entry):
    return [
        term
        for term in (
            entry["name_zh"],
            entry["name_en"],
            *entry.get("aliases_zh", []),
            *entry.get("aliases_en", []),
        )
    ]


def test_dictionary_size_and_required_fields(ingredients_json):
    assert 180 <= len(ingredients_json) <= 230
    for entry in ingredients_json:
        assert entry["name_zh"] and entry["name_en"]
        assert entry["category"] in CATEGORIES
        assert entry["stock_mode"] in STOCK_MODES
        assert entry["allergens"] is not None
        assert set(entry["allergens"]) <= set(ALLERGENS)


def test_no_name_or_alias_maps_to_two_entries(ingredients_json):
    owners = {}
    for entry in ingredients_json:
        for term in {term.strip().lower() for term in terms_of(entry)}:
            assert term not in owners, f"{term} is on {owners[term]} and {entry['name_zh']}"
            owners[term] = entry["name_zh"]


def test_children_carry_their_parents_allergens(ingredients_json):
    by_name = {entry["name_zh"]: entry for entry in ingredients_json}
    for entry in ingredients_json:
        if entry.get("parent"):
            missing = set(by_name[entry["parent"]]["allergens"]) - set(entry["allergens"])
            assert not missing, f"{entry['name_zh']} is missing {missing}"


def test_hierarchy_has_one_level_and_design_examples(seeded):
    assert {child.name_zh for child in by_zh(seeded, "酱油").children} >= {"生抽", "老抽"}
    assert by_zh(seeded, "五花肉").parent.name_zh == "猪肉"
    parent = aliased(Ingredient)
    query = select(func.count()).select_from(Ingredient).join(parent, Ingredient.parent)
    query = query.where(parent.parent_id.is_not(None))
    assert seeded.scalar(query) == 0


def test_kinds_are_not_aliases_and_forms_are(seeded):
    assert "生抽" not in by_zh(seeded, "酱油").aliases_zh
    assert "老抽" not in by_zh(seeded, "酱油").aliases_zh
    assert "葱花" in by_zh(seeded, "葱").aliases_zh


def test_design_allergen_tags(seeded):
    assert "crustaceans" in by_zh(seeded, "海米").allergens
    assert "shellfish" in by_zh(seeded, "蚝油").allergens
    assert {"soy", "wheat"} <= set(by_zh(seeded, "酱油").allergens)
    assert "peanut" in by_zh(seeded, "花生").allergens
    assert "tree nut" in by_zh(seeded, "核桃").allergens
    assert "peanut" not in by_zh(seeded, "核桃").allergens


def test_reviewed_hierarchy_and_names(seeded):
    assert {"口蘑", "香菇", "金针菇"} <= {c.name_zh for c in by_zh(seeded, "蘑菇").children}
    assert {"四季豆", "豇豆", "油豆角"} <= {c.name_zh for c in by_zh(seeded, "豆角").children}
    assert {"白胡椒", "黑胡椒"} <= {c.name_zh for c in by_zh(seeded, "胡椒").children}
    assert "白蘑菇" in by_zh(seeded, "口蘑").aliases_zh
    assert "蘑菇" not in by_zh(seeded, "口蘑").aliases_zh
    assert "金针蘑" in by_zh(seeded, "金针菇").aliases_zh
    assert "青笋" in by_zh(seeded, "莴笋").aliases_zh
    assert by_zh(seeded, "大豆油").parent.name_zh == "食用油"


def test_reviewed_conservative_allergen_tags(seeded):
    assert "soy" in by_zh(seeded, "大豆油").allergens
    assert "wheat" in by_zh(seeded, "陈醋").allergens
    assert "wheat" in by_zh(seeded, "香醋").allergens
    assert {"egg", "soy", "wheat"} <= set(by_zh(seeded, "鸡精").allergens)
    assert "soy" in by_zh(seeded, "火腿").allergens
    assert "soy" in by_zh(seeded, "培根").allergens
    assert "wheat" in by_zh(seeded, "燕麦").allergens
    assert {"sesame", "peanut", "fish", "crustaceans"} <= set(by_zh(seeded, "火锅底料").allergens)
    assert {"peanut", "sesame"} <= set(by_zh(seeded, "辣椒酱").allergens)
    assert "sesame" in by_zh(seeded, "沙茶酱").allergens


def test_seed_rows_are_marked_as_seed(seeded):
    assert by_zh(seeded, "葱").source == "seed"


def test_always_in_stock_defaults(seeded):
    assert {"盐", "食用油", "水", "糖", "酱油", "醋"} <= always_in_stock_names(seeded)


def test_cookware_alias(seeded):
    wok = seeded.scalars(select(Cookware).where(Cookware.name_zh == "炒锅")).one()
    assert "wok" in [alias.lower() for alias in [wok.name_en, *wok.aliases_en]]
    assert wok.source == "seed"


def test_cookware_seed_has_no_basic_utensils():
    # Every kitchen has these, so H3 checking them would only add noise.
    names = {entry["name_zh"] for entry in json.loads((SEED_DIR / "cookware.json").read_text("utf-8"))}
    assert not names & {"案板", "菜刀", "勺子", "筷子", "锅铲", "碗"}
    assert {"炒锅", "烤箱", "蒸锅"} <= names


def test_reseeding_keeps_user_data(seeded):
    user_entry = Ingredient(name_zh="秘制酱", category="other", stock_mode="level", source="user")
    seeded.add(user_entry)
    vinegar = seeded.get(AlwaysInStock, by_zh(seeded, "醋").id)
    seeded.delete(vinegar)
    seeded.flush()

    seed_all(seeded)
    seeded.flush()

    assert by_zh(seeded, "秘制酱").id == user_entry.id
    assert "醋" not in always_in_stock_names(seeded)
    assert seeded.scalar(select(func.count()).select_from(Diner).where(Diner.is_self)) == 1


def test_reseeding_updates_seed_rows(seeded):
    entry = by_zh(seeded, "葱")
    entry.aliases_zh = []
    seeded.flush()
    seed_all(seeded)
    seeded.flush()
    assert "葱花" in by_zh(seeded, "葱").aliases_zh


def test_seed_refuses_to_overwrite_a_user_entry(session):
    session.add(Ingredient(name_zh="葱", category="other", stock_mode="level", source="user"))
    session.flush()
    with pytest.raises(ValueError, match="葱"):
        seed_all(session)
