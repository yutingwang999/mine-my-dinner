"""keep recipe sources

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('recipes', sa.Column('source_text', sa.Text(), nullable=True))
    op.add_column(
        'extraction_logs',
        sa.Column(
            'image_files', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False
        ),
    )
    op.create_table('recipe_images',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recipe_id', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.Text(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recipe_images_file_name'), 'recipe_images', ['file_name'], unique=False)
    op.create_index(op.f('ix_recipe_images_recipe_id'), 'recipe_images', ['recipe_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_recipe_images_recipe_id'), table_name='recipe_images')
    op.drop_index(op.f('ix_recipe_images_file_name'), table_name='recipe_images')
    op.drop_table('recipe_images')
    op.drop_column('extraction_logs', 'image_files')
    op.drop_column('recipes', 'source_text')
