"""Store configurable diagnostic questions and combination results.

Revision ID: 95df03b4a721
Revises: 4db6427f4417
"""
from alembic import op
import sqlalchemy as sa

revision = '95df03b4a721'
down_revision = '4db6427f4417'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tracker_tasks', sa.Column('diagnostic_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('tracker_tasks', 'diagnostic_config')
