"""add curator_tag to users

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa


revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("curator_tag", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "curator_tag")
