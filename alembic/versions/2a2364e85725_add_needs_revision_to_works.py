"""add needs_revision to works

Revision ID: 2a2364e85725
Revises: b3c4d5e6f7a8
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa

revision = '2a2364e85725'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('works',
        sa.Column('needs_revision', sa.Boolean(), server_default='false', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('works', 'needs_revision')
