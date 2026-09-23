"""add started_at to tracker_task_states

Revision ID: dadc28ad26d8
Revises: 95df03b4a721
Create Date: 2026-09-24

"""
from alembic import op
import sqlalchemy as sa

revision = 'dadc28ad26d8'
down_revision = '95df03b4a721'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'tracker_task_states',
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tracker_task_states', 'started_at')
