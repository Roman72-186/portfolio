"""feedback_message audio columns + расширение CHECK на голосовые

Revision ID: b2c3d4e5f6a7
Revises: d499b1aad9ba
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa


revision = "5e71d0328939"
down_revision = "d499b1aad9ba"
branch_labels = None
depends_on = None

_OLD_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL)"
)
_NEW_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL) "
    "OR (audio_s3_url IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(
        "feedback_messages",
        sa.Column("audio_s3_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "feedback_messages",
        sa.Column("audio_s3_url", sa.String(length=500), nullable=True),
    )
    # Postgres не меняет CHECK на месте — drop + create.
    op.drop_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", _NEW_CK
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", _OLD_CK
    )
    op.drop_column("feedback_messages", "audio_s3_url")
    op.drop_column("feedback_messages", "audio_s3_path")
