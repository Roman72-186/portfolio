"""learning video catalog

Revision ID: 7f4e3d2c1b0a
Revises: 9d8c7b6a5f4e
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f4e3d2c1b0a"
down_revision: Union[str, None] = "9d8c7b6a5f4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    learning_videos = op.create_table(
        "learning_videos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bunny_library_id", sa.BigInteger(), nullable=False),
        sa.Column("bunny_video_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("original_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("original_mime_type", sa.String(length=100), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("bunny_status", sa.Integer(), nullable=True),
        sa.Column("encode_progress", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status_message", sa.String(length=500), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bunny_video_id"),
    )
    op.create_index(
        "ix_learning_videos_public",
        "learning_videos",
        ["is_published", "status", "sort_order"],
    )
    op.create_index("ix_learning_videos_status", "learning_videos", ["status"])

    op.bulk_insert(
        learning_videos,
        [
            {
                "bunny_library_id": 720058,
                "bunny_video_id": "35ed80ae-8103-4528-a700-3f69ec56957d",
                "title": "Тестовый видеоурок",
                "sort_order": 0,
                "status": "ready",
                "bunny_status": 3,
                "encode_progress": 100,
                "is_published": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_learning_videos_status", table_name="learning_videos")
    op.drop_index("ix_learning_videos_public", table_name="learning_videos")
    op.drop_table("learning_videos")
