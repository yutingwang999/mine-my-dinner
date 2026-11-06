import pytest

from minemydinner.models import DietaryRule, Diner, Ingredient
from minemydinner.recipes.allergy import (
    LineRef,
    allergen_info,
    find_allergy_conflicts,
    find_unknown_allergen_lines,
)
from minemydinner.seed import ensure_self_diner
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import by_zh, lines


def user_ingredient(session, allergens, parent=None, name="秘制酱") -> Ingredient:
    entry = Ingredient(
        name_zh=name,
        category="seasoning",
        stock_mode="level",
        source="user",
        allergens=allergens,
        parent=parent,
    )
    session.add(entry)
    session.flush()
    return entry


def add_rule(session, diner, rule_type="allergy", allergen=None, ingredient=None) -> None:
    session.add(
        DietaryRule(
            diner_id=diner.id,
            rule_type=rule_type,
            allergen=allergen,
            ingredient_id=ingredient.id if ingredient else None,
        )
    )
    session.flush()


def line_of(ingredient: Ingredient) -> LineRef:
    return LineRef(name=ingredient.name_zh, ingredient_id=ingredient.id)


@pytest.fixture
def self_diner(seeded):
    return ensure_self_diner(seeded)


@pytest.fixture
def self_allergy_crustaceans(seeded, self_diner):
    add_rule(seeded, self_diner, allergen="crustaceans")


@pytest.fixture
def friend_allergy_crustaceans(seeded, self_diner):
    friend = Diner(display_name="小李")
    seeded.add(friend)
    seeded.flush()
    add_rule(seeded, friend, allergen="crustaceans")


def test_child_inherits_parent_allergens(seeded):
    assert "soy" in allergen_info(by_zh(seeded, "生抽")).known


def test_null_is_unknown_and_empty_is_confirmed_none(seeded):
    assert allergen_info(user_ingredient(seeded, allergens=None)).unknown is True
    info = allergen_info(user_ingredient(seeded, allergens=[], name="清水"))
    assert info.unknown is False and info.known == frozenset()


def test_child_with_null_keeps_parents_known_and_is_unknown(seeded):
    info = allergen_info(user_ingredient(seeded, allergens=None, parent=by_zh(seeded, "酱油")))
    assert "soy" in info.known and info.unknown is True


def test_child_of_unknown_parent_is_unknown(seeded):
    parent = user_ingredient(seeded, allergens=None, name="秘制酱")
    assert allergen_info(user_ingredient(seeded, [], parent=parent, name="秘制酱汁")).unknown


def test_tag_rule_conflict_warns_and_does_not_block(
    client, seeded, self_allergy_crustaceans, fake_llm, recipe_write_with_haimi
):
    body = client.post("/api/recipes/allergy-check", json=lines(recipe_write_with_haimi)).json()
    assert body["conflicts"][0]["allergen"] == "crustaceans"
    assert body["conflicts"][0]["line_name"] == "海米"
    assert client.post("/api/recipes", json=recipe_write_with_haimi).status_code == 201


def test_ingredient_rule_on_parent_covers_child(session, seeded, self_diner):
    add_rule(session, self_diner, ingredient=by_zh(session, "猪肉"))
    conflicts = find_allergy_conflicts(session, [self_diner.id], [line_of(by_zh(session, "五花肉"))])
    assert conflicts and conflicts[0].ingredient_id == by_zh(session, "猪肉").id


def test_ingredient_rule_on_child_does_not_cover_parent(session, seeded, self_diner):
    add_rule(session, self_diner, ingredient=by_zh(session, "五花肉"))
    assert not find_allergy_conflicts(session, [self_diner.id], [line_of(by_zh(session, "猪肉"))])


def test_dislikes_are_not_allergy_conflicts(session, seeded, self_diner):
    add_rule(session, self_diner, rule_type="dislike", ingredient=by_zh(session, "猪肉"))
    assert not find_allergy_conflicts(session, [self_diner.id], [line_of(by_zh(session, "猪肉"))])


def test_friend_rules_do_not_trigger_self_check(
    client, seeded, friend_allergy_crustaceans, recipe_write_with_haimi
):
    body = client.post("/api/recipes/allergy-check", json=lines(recipe_write_with_haimi)).json()
    assert body["conflicts"] == []


def test_unmatched_line_is_unknown_not_safe(client, seeded, recipe_write):
    body = client.post("/api/recipes/allergy-check", json=lines(recipe_write)).json()
    assert body["unknown"] == ["秘制酱"]


def test_unknown_lines_include_entries_with_null_allergens(session, seeded):
    entry = user_ingredient(session, allergens=None)
    found = find_unknown_allergen_lines(
        session, [line_of(entry), line_of(by_zh(session, "葱")), LineRef("怪菜", None)]
    )
    assert found == ["秘制酱", "怪菜"]
