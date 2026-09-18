"""Per-block portfolio requirement for intake students.

Revision ID: e4b7c9d1a2f3
Revises: c41e8b719d2a
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa


revision = "e4b7c9d1a2f3"
down_revision = "c41e8b719d2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_blocks",
        sa.Column(
            "is_required_for_intake",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("task_blocks", "is_required_for_intake")
