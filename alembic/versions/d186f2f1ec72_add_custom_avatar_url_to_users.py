"""add custom_avatar_url to users

Revision ID: d186f2f1ec72
Revises: 7cee183db760
Create Date: 2026-08-20

"""
from alembic import op
import sqlalchemy as sa

revision = 'd186f2f1ec72'
down_revision = '7cee183db760'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users',
        sa.Column('custom_avatar_url', sa.String(length=500), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('users', 'custom_avatar_url')
