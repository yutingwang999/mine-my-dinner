"""record cached and cache-written input tokens on llm_calls

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('llm_calls', sa.Column('cached_input_tokens', sa.Integer(), nullable=True))
    op.add_column('llm_calls', sa.Column('cache_write_input_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('llm_calls', 'cache_write_input_tokens')
    op.drop_column('llm_calls', 'cached_input_tokens')
