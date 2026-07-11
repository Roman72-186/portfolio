"""add last_login_at + onboarding timestamps to users

Revision ID: b5c6d7e8f9a0
Revises: 0a1b2c3d4e5f
Create Date: 2026-07-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("portfolio_do_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "portfolio_do_completed_at")
    op.drop_column("users", "profile_completed_at")
    op.drop_column("users", "last_login_at")
