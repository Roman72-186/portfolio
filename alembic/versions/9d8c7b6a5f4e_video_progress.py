"""video progress

Revision ID: 9d8c7b6a5f4e
Revises: 13ee8d6041e3
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "9d8c7b6a5f4e"
down_revision = "13ee8d6041e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_progress",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("video_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("position_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("video_progress")
