"""add needs_revision_at to homework_submissions

Revision ID: 4ae61acc0751
Revises: a9f1c3e7d2b4
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa

revision = '4ae61acc0751'
down_revision = 'a9f1c3e7d2b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'homework_submissions',
        sa.Column('needs_revision_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('homework_submissions', 'needs_revision_at')
