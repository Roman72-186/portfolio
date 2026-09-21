"""add needs_revision to task_block_submissions

Revision ID: 4db6427f4417
Revises: 4ae61acc0751
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa

revision = '4db6427f4417'
down_revision = '4ae61acc0751'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'task_block_submissions',
        sa.Column('needs_revision', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'task_block_submissions',
        sa.Column('needs_revision_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('task_block_submissions', 'needs_revision_at')
    op.drop_column('task_block_submissions', 'needs_revision')
