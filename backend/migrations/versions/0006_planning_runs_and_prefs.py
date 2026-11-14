"""add planning runs and app preferences

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'planning_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thread_id', sa.Text(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column('prompt_version', sa.Text(), nullable=False),
        sa.Column('tools_version', sa.Text(), nullable=False),
        sa.Column('request', JSONB(), nullable=False),
        sa.Column('defaults_used', ARRAY(sa.Text()), nullable=False),
        sa.Column('events', JSONB(), server_default='[]', nullable=False),
        sa.Column('retries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('first_passed', sa.Boolean()),
        sa.Column('final_passed', sa.Boolean()),
        sa.Column('final_plan', JSONB()),
        sa.Column('has_temporary_guests', sa.Boolean(), server_default='false', nullable=False),
    )
    op.create_index('ix_planning_runs_thread_id', 'planning_runs', ['thread_id'])
    op.create_table(
        'app_preferences',
        sa.Column('key', sa.Text(), primary_key=True),
        sa.Column('value', JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('app_preferences')
    op.drop_index('ix_planning_runs_thread_id', table_name='planning_runs')
    op.drop_table('planning_runs')
