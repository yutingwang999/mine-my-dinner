from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from minemydinner.constants import EMBEDDING_DIMENSION
from minemydinner.llm.client import AllModelsFailed, MonthlyCapExceeded

from minemydinner.models import (
    AlwaysInStock,
    CookwareOwned,
    DietaryRule,
    Diner,
    Inventory,
    Recipe,
    RecipeCookware,
    RecipeIngredient,
)
from minemydinner.planning.context import (
    MealConstraints,
    PlanningContext,
    RuleRef,
    load_planning_context,
)
from tests.recipefakes import by_zh, cookware_id


@dataclass
class L:
    """One recipe line. `linked=False` leaves the line without a dictionary entry."""

    name: str
    role: str = "secondary"
    optional: bool = False
    choice_group: int | None = None
    linked: bool = True


def make_recipe(
    session,
    title,
    dish_type="vegetable dish",
    lines=(),
    cookware=(),
    time_min=None,
    time_source=None,
    servings=None,
    embedding=None,
) -> Recipe:
    recipe = Recipe(
        lang="zh",
        title=title,
        dish_type=dish_type,
        time_min=time_min,
        time_source=time_source,
        servings=servings,
        source_type="manual",
        embedding=embedding,
    )
    for item in lines:
        recipe.ingredients.append(
            RecipeIngredient(
                raw=item.name,
                name=item.name,
                ingredient_id=by_zh(session, item.name).id if item.linked else None,
                amount_type="vague",
                role=item.role,
                optional=item.optional,
                choice_group=item.choice_group,
            )
        )
    for name in cookware:
        recipe.cookware.append(RecipeCookware(cookware_id=cookware_id(session, name)))
    session.add(recipe)
    session.flush()
    return recipe


def stock(session, name_zh, count=None, level=None, grams=None, expires_on: date | None = None):
    row = Inventory(
        ingredient_id=by_zh(session, name_zh).id,
        count=count,
        level=level,
        grams=grams,
        expires_on=expires_on,
    )
    session.add(row)
    session.flush()
    return row


def not_always_in_stock(session, name_zh) -> None:
    """Remove an ingredient from the always-in-stock list (seeding puts 酱油 there)."""
    session.execute(
        delete(AlwaysInStock).where(AlwaysInStock.ingredient_id == by_zh(session, name_zh).id)
    )
    session.flush()


def always_in_stock(session, name_zh) -> None:
    session.add(AlwaysInStock(ingredient_id=by_zh(session, name_zh).id))
    session.flush()


def own_cookware(session, name_zh) -> None:
    session.add(CookwareOwned(cookware_id=cookware_id(session, name_zh)))
    session.flush()


def rule(session, diner_id, rule_type, allergen=None, ingredient=None, text=None) -> DietaryRule:
    row = DietaryRule(
        diner_id=diner_id,
        rule_type=rule_type,
        allergen=allergen,
        ingredient_id=by_zh(session, ingredient).id if ingredient else None,
        text=text,
    )
    session.add(row)
    session.flush()
    return row


def rule_ref(
    rule_type, allergen=None, ingredient_id=None, text=None, diner_key="diner:1"
) -> RuleRef:
    return RuleRef(diner_key, rule_type, allergen, ingredient_id, text)


def self_diner(session) -> Diner:
    existing = session.scalars(select(Diner).where(Diner.is_self)).first()
    if existing:
        return existing
    diner = Diner(display_name="Me", is_self=True)
    session.add(diner)
    session.flush()
    return diner


def ctx_for(session, *diner_ids, **kwargs) -> PlanningContext:
    return load_planning_context(session, MealConstraints(diner_ids=tuple(diner_ids), **kwargs))


def unit_vector(position: int) -> list[float]:
    """A vector with a single 1.0 at `position`, for ranking by cosine distance."""
    vector = [0.0] * EMBEDDING_DIMENSION
    vector[position] = 1.0
    return vector


def blend_vector(position: int, other: int, weight: float) -> list[float]:
    """Mostly `position`, with `weight` of `other` mixed in."""
    vector = unit_vector(position)
    vector[other] = weight
    return vector


class FakeEmbedClient:
    """Stands in for LLMClient in search tests. No network, no model."""

    def __init__(self, vector: list[float] | None = None, error: Exception | None = None):
        self.vector = vector if vector is not None else unit_vector(0)
        self.error = error
        self.embedded: list[str] = []
        self.tasks: list[str] = []

    def embed(self, texts, task="embeddings"):
        if self.error is not None:
            raise self.error
        self.embedded.extend(texts)
        self.tasks.append(task)
        return [list(self.vector) for _ in texts]


def over_cap_client() -> FakeEmbedClient:
    return FakeEmbedClient(error=MonthlyCapExceeded("over"))


def all_failed_client() -> FakeEmbedClient:
    return FakeEmbedClient(error=AllModelsFailed("down"))


def rollback_session_factory(session):
    """A sessionmaker on the test connection. Its sessions join the test transaction through a
    savepoint, so a tool's commit never reaches the database."""
    return sessionmaker(bind=session.get_bind(), join_transaction_mode="create_savepoint")
