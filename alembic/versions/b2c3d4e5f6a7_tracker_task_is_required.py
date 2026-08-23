"""tracker_tasks.is_required — обязательность элемента для гейта недели/месяца

Revision ID: b2c3d4e5f6a7
Revises: 34e254ad8ba5
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "34e254ad8ba5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tracker_tasks",
        sa.Column(
            "is_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tracker_tasks", "is_required")
