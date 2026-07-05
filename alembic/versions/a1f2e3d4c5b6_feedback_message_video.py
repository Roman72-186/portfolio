"""feedback_message video columns + расширение CHECK на видео

Revision ID: a1f2e3d4c5b6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa


revision = "a1f2e3d4c5b6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

_OLD_CK = "(text IS NOT NULL AND length(text) > 0) OR (photo_s3_url IS NOT NULL)"
_NEW_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(
        "feedback_messages",
        sa.Column("video_s3_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "feedback_messages",
        sa.Column("video_s3_url", sa.String(length=500), nullable=True),
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
    op.drop_column("feedback_messages", "video_s3_url")
    op.drop_column("feedback_messages", "video_s3_path")
