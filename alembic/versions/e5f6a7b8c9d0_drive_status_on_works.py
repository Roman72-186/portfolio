"""add drive_status to works

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-28

"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('works',
        sa.Column('drive_status', sa.String(20), server_default='pending', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('works', 'drive_status')
