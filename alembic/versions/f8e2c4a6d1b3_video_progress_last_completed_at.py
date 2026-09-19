"""Track the latest confirmed video completion.

Revision ID: f8e2c4a6d1b3
Revises: e4b7c9d1a2f3
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa


revision = "f8e2c4a6d1b3"
down_revision = "e4b7c9d1a2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_progress",
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "video_progress",
        sa.Column(
            "last_completion_watched_seconds",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        "UPDATE video_progress "
        "SET last_completed_at = completed_at, "
        "last_completion_watched_seconds = watched_seconds "
        "WHERE completed_at IS NOT NULL"
    )
    op.alter_column(
        "video_progress",
        "last_completion_watched_seconds",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("video_progress", "last_completion_watched_seconds")
    op.drop_column("video_progress", "last_completed_at")
