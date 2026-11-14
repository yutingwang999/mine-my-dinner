"""add personal aliases to ingredients

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ('user_aliases_zh', 'user_aliases_en'):
        op.add_column(
            'ingredients',
            sa.Column(column, ARRAY(sa.Text()), server_default='{}', nullable=False),
        )


def downgrade() -> None:
    op.drop_column('ingredients', 'user_aliases_en')
    op.drop_column('ingredients', 'user_aliases_zh')
