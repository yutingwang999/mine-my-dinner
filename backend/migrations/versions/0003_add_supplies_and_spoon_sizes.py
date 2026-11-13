"""add supplies and spoon sizes

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNITS = "unit IN ('g', 'ml', 'piece', 'stalk', 'spoon')"
NEW_UNITS = "unit IN ('g', 'ml', 'piece', 'stalk', 'tablespoon', 'teaspoon', 'spoon')"


def upgrade() -> None:
    op.add_column(
        'recipes',
        sa.Column('supplies', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    )
    op.drop_constraint('recipe_ingredient_unit_valid', 'recipe_ingredients', type_='check')
    op.create_check_constraint('recipe_ingredient_unit_valid', 'recipe_ingredients', NEW_UNITS)


def downgrade() -> None:
    op.drop_constraint('recipe_ingredient_unit_valid', 'recipe_ingredients', type_='check')
    op.create_check_constraint('recipe_ingredient_unit_valid', 'recipe_ingredients', OLD_UNITS)
    op.drop_column('recipes', 'supplies')
