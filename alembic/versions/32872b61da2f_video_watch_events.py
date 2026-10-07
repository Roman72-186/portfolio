"""video watch events — срезы и отказы зачёта видео (07.10.2026)

Новая таблица, существующие не трогает. Откат — снос таблицы целиком.

Revision ID: 32872b61da2f
Revises: 24a3a585a53b
Create Date: 2026-10-07 09:02:55.218643

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32872b61da2f'
down_revision: Union[str, None] = '24a3a585a53b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "video_watch_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("block_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position_from", sa.Float(), nullable=True),
        sa.Column("position_to", sa.Float(), nullable=True),
        sa.Column("skipped_seconds", sa.Float(), nullable=True),
        sa.Column("gap_seconds", sa.Float(), nullable=True),
        sa.Column("playing", sa.Boolean(), nullable=True),
        sa.Column("watched_seconds", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_video_watch_events_user_video", "video_watch_events",
        ["user_id", "video_id", "created_at"],
    )
    op.create_index(
        "ix_video_watch_events_created_at", "video_watch_events", ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_watch_events_created_at", table_name="video_watch_events")
    op.drop_index("ix_video_watch_events_user_video", table_name="video_watch_events")
    op.drop_table("video_watch_events")
