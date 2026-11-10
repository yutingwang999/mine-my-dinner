from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from minemydinner.constants import (
    ALLERGENS,
    AMOUNT_TYPES,
    CATEGORIES,
    DICTIONARY_SOURCES,
    DISH_TYPES,
    EMBEDDING_DIMENSION,
    LANGUAGES,
    ROLES,
    SOURCE_TYPES,
    STOCK_MODES,
    TIME_SOURCES,
    UNITS,
)
from minemydinner.models.base import Base, array_literal, in_list


class Ingredient(Base):
    __tablename__ = "ingredients"
    __table_args__ = (
        CheckConstraint("name_zh IS NOT NULL OR name_en IS NOT NULL", name="ingredient_has_name"),
        CheckConstraint(in_list("category", CATEGORIES), name="ingredient_category_valid"),
        CheckConstraint(in_list("stock_mode", STOCK_MODES), name="ingredient_stock_mode_valid"),
        CheckConstraint(in_list("source", DICTIONARY_SOURCES), name="ingredient_source_valid"),
        CheckConstraint(
            f"allergens <@ {array_literal(ALLERGENS)}", name="ingredient_allergens_valid"
        ),
        CheckConstraint(
            "source <> 'seed' OR "
            "(allergens IS NOT NULL AND name_zh IS NOT NULL AND name_en IS NOT NULL)",
            name="ingredient_seed_complete",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name_zh: Mapped[str | None] = mapped_column(Text, unique=True)
    name_en: Mapped[str | None] = mapped_column(Text, unique=True)
    aliases_zh: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    aliases_en: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    stock_mode: Mapped[str] = mapped_column(Text, nullable=False)
    # Empty means confirmed none. Null means unknown.
    allergens: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("ingredients.id", ondelete="RESTRICT"))
    source: Mapped[str] = mapped_column(Text, nullable=False)

    parent: Mapped["Ingredient | None"] = relationship(
        remote_side="Ingredient.id", back_populates="children"
    )
    children: Mapped[list["Ingredient"]] = relationship(
        back_populates="parent", passive_deletes="all"
    )


class Cookware(Base):
    __tablename__ = "cookware"
    __table_args__ = (
        CheckConstraint("name_zh IS NOT NULL OR name_en IS NOT NULL", name="cookware_has_name"),
        CheckConstraint(in_list("source", DICTIONARY_SOURCES), name="cookware_source_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name_zh: Mapped[str | None] = mapped_column(Text, unique=True)
    name_en: Mapped[str | None] = mapped_column(Text, unique=True)
    aliases_zh: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    aliases_en: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class Recipe(Base):
    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint(in_list("lang", LANGUAGES), name="recipe_lang_valid"),
        CheckConstraint(in_list("dish_type", DISH_TYPES), name="recipe_dish_type_valid"),
        CheckConstraint(in_list("time_source", TIME_SOURCES), name="recipe_time_source_valid"),
        CheckConstraint(in_list("source_type", SOURCE_TYPES), name="recipe_source_type_valid"),
        CheckConstraint(
            "time_min IS NOT NULL OR time_source IS NULL", name="recipe_time_source_has_time"
        ),
        Index(
            "ix_recipes_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lang: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    dish_type: Mapped[str] = mapped_column(Text, nullable=False)
    time_min: Mapped[int | None] = mapped_column(Integer)
    time_source: Mapped[str | None] = mapped_column(Text)
    servings: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    steps_summary: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    # The pasted text or batch part, kept as the full method. See design 4.2.
    source_text: Mapped[str | None] = mapped_column(Text)
    parent_recipe_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipes.id", ondelete="SET NULL")
    )
    variant_note: Mapped[str | None] = mapped_column(Text)
    # Null until computed, so a failed embedding call never loses a recipe.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RecipeIngredient.id",
    )
    cookware: Mapped[list["RecipeCookware"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", passive_deletes=True
    )
    images: Mapped[list["RecipeImage"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RecipeImage.position",
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        CheckConstraint(in_list("unit", UNITS), name="recipe_ingredient_unit_valid"),
        CheckConstraint(
            in_list("amount_type", AMOUNT_TYPES), name="recipe_ingredient_amount_type_valid"
        ),
        CheckConstraint(in_list("role", ROLES), name="recipe_ingredient_role_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredients.id", ondelete="SET NULL"), index=True
    )
    amount: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(Text)
    amount_type: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    optional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient | None] = relationship()


class RecipeCookware(Base):
    __tablename__ = "recipe_cookware"

    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    cookware_id: Mapped[int] = mapped_column(
        ForeignKey("cookware.id", ondelete="CASCADE"), primary_key=True
    )

    recipe: Mapped[Recipe] = relationship(back_populates="cookware")
    cookware: Mapped[Cookware] = relationship()


class RecipeImage(Base):
    """A screenshot kept as the recipe's full method. The file lives in the image folder."""

    __tablename__ = "recipe_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The content hash plus extension, so the same image is stored once.
    file_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    recipe: Mapped[Recipe] = relationship(back_populates="images")
