"""Personal portfolio upload window in hours.

Revision ID: c41e8b719d2a
Revises: a7cdccec5065
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa


revision = "c41e8b719d2a"
down_revision = "a7cdccec5065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_blocks",
        sa.Column("portfolio_window_hours", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_blocks", "portfolio_window_hours")
