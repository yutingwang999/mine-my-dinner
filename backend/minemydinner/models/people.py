from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from minemydinner.constants import ALLERGENS, RULE_TYPES, STOCK_LEVELS
from minemydinner.models.base import Base, in_list
from minemydinner.models.recipes import Cookware, Ingredient


class Diner(Base):
    __tablename__ = "diners"
    __table_args__ = (
        Index("uq_diners_one_self", "is_self", unique=True, postgresql_where=text("is_self")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_self: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    rules: Mapped[list["DietaryRule"]] = relationship(
        back_populates="diner", cascade="all, delete-orphan", passive_deletes=True
    )


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (CheckConstraint(in_list("level", STOCK_LEVELS), name="inventory_level_valid"),)

    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    count: Mapped[int | None] = mapped_column(Integer)
    level: Mapped[str | None] = mapped_column(Text)
    grams: Mapped[float | None] = mapped_column(Float)
    expires_on: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    ingredient: Mapped[Ingredient] = relationship()


class CookwareOwned(Base):
    __tablename__ = "cookware_owned"

    cookware_id: Mapped[int] = mapped_column(
        ForeignKey("cookware.id", ondelete="CASCADE"), primary_key=True
    )

    cookware: Mapped[Cookware] = relationship()


class AlwaysInStock(Base):
    __tablename__ = "always_in_stock"

    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )

    ingredient: Mapped[Ingredient] = relationship()


class DietaryRule(Base):
    __tablename__ = "dietary_rules"
    __table_args__ = (
        CheckConstraint(in_list("rule_type", RULE_TYPES), name="dietary_rule_type_valid"),
        CheckConstraint(in_list("allergen", ALLERGENS), name="dietary_rule_allergen_valid"),
        CheckConstraint(
            "num_nonnulls(allergen, ingredient_id, text) = 1", name="dietary_rule_one_target"
        ),
        CheckConstraint(
            "rule_type <> 'allergy' OR text IS NULL", name="dietary_rule_allergy_no_free_text"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    diner_id: Mapped[int] = mapped_column(
        ForeignKey("diners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_type: Mapped[str] = mapped_column(Text, nullable=False)
    allergen: Mapped[str | None] = mapped_column(Text)
    # RESTRICT: deleting a dictionary entry must not silently remove an allergy rule.
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT")
    )
    text: Mapped[str | None] = mapped_column(Text)

    diner: Mapped[Diner] = relationship(back_populates="rules")
    ingredient: Mapped[Ingredient | None] = relationship()
