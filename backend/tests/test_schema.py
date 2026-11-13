import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from minemydinner.models import (
    DietaryRule,
    Diner,
    Ingredient,
    MealPlan,
    MealPlanItem,
    Recipe,
)


@pytest.fixture
def self_diner(session):
    diner = Diner(display_name="Me", is_self=True)
    session.add(diner)
    session.flush()
    return diner


def make_recipe(title: str, **fields) -> Recipe:
    return Recipe(lang="zh", title=title, dish_type="meat dish", source_type="text", **fields)


@pytest.fixture
def recipe_in_plan_with_variant(session):
    recipe = make_recipe("红烧肉")
    session.add(recipe)
    session.flush()
    variant = make_recipe("红烧鸡", parent_recipe_id=recipe.id)
    plan = MealPlan(thread_id="thread", constraints={"headcount": 1}, edits=[])
    session.add_all([variant, plan])
    session.flush()
    item = MealPlanItem(plan_id=plan.id, recipe_id=recipe.id, recipe_title=recipe.title, servings=2)
    session.add(item)
    session.flush()
    return recipe, item, variant


def test_seed_entry_rejects_null_allergens(session):
    session.add(
        Ingredient(
            name_zh="测试",
            name_en="test",
            category="other",
            stock_mode="level",
            allergens=None,
            source="seed",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_seed_entry_needs_both_names(session):
    session.add(
        Ingredient(name_zh="测试", category="other", stock_mode="level", allergens=[], source="seed")
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_entry_needs_a_name(session):
    session.add(Ingredient(category="other", stock_mode="level", source="user"))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("value", [None, []])
def test_user_entry_allows_null_and_empty_allergens(session, value):
    session.add(
        Ingredient(
            name_zh="秘制酱", category="other", stock_mode="level", allergens=value, source="user"
        )
    )
    session.flush()


def test_allergens_reject_values_outside_the_list(session):
    session.add(
        Ingredient(
            name_zh="测试", category="other", stock_mode="level", allergens=["shrimp"], source="user"
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_dietary_rule_needs_exactly_one_target(session, self_diner):
    session.add(DietaryRule(diner_id=self_diner.id, rule_type="allergy", allergen="egg", text="x"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_dietary_rule_needs_a_target(session, self_diner):
    session.add(DietaryRule(diner_id=self_diner.id, rule_type="dislike"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_allergy_rule_rejects_free_text(session, self_diner):
    session.add(DietaryRule(diner_id=self_diner.id, rule_type="allergy", text="虾"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_allergy_rule_rejects_unlisted_allergen(session, self_diner):
    session.add(DietaryRule(diner_id=self_diner.id, rule_type="allergy", allergen="shrimp"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_ingredient_used_by_a_rule_cannot_be_deleted(session, self_diner):
    ingredient = Ingredient(name_zh="芒果", category="fruit", stock_mode="count", source="user")
    session.add(ingredient)
    session.flush()
    session.add(DietaryRule(diner_id=self_diner.id, rule_type="allergy", ingredient_id=ingredient.id))
    session.flush()
    session.delete(ingredient)
    with pytest.raises(IntegrityError):
        session.flush()


def test_only_one_self_diner(session, self_diner):
    session.add(Diner(display_name="Other", is_self=True))
    with pytest.raises(IntegrityError):
        session.flush()


def test_time_source_requires_time(session):
    session.add(make_recipe("红烧肉", time_min=None, time_source="stated"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_recipe_keeps_plan_item_title_and_variant(session, recipe_in_plan_with_variant):
    recipe, item, variant = recipe_in_plan_with_variant
    title = recipe.title
    session.delete(recipe)
    session.flush()
    session.expire_all()
    assert item.recipe_id is None
    assert item.recipe_title == title
    assert variant.parent_recipe_id is None


def test_embedding_dimension_and_hnsw_index(session):
    query = text(
        "SELECT indexdef FROM pg_indexes WHERE tablename='recipes' AND indexdef ILIKE '%hnsw%'"
    )
    assert "vector_cosine_ops" in session.execute(query).scalar()
    assert Recipe.__table__.c.embedding.type.dim == 768
