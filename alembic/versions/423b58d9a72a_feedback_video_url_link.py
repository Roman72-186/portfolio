"""video_url в feedback_messages и homework_feedback_messages — ссылка на
видео как альтернатива загрузке файла (владелец 10.09.2026, созвон 09-10.09,
сжатие видео отдельной задачей без срока).

Revision ID: 423b58d9a72a
Revises: da35b5b9d126
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa


revision = "423b58d9a72a"
down_revision = "da35b5b9d126"
branch_labels = None
depends_on = None

_FEEDBACK_OLD_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL) "
    "OR (audio_s3_url IS NOT NULL)"
)
_FEEDBACK_NEW_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL) "
    "OR (audio_s3_url IS NOT NULL) OR (video_url IS NOT NULL)"
)

_HOMEWORK_OLD_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL)"
)
_HOMEWORK_NEW_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL) "
    "OR (video_url IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(
        "feedback_messages",
        sa.Column("video_url", sa.String(length=500), nullable=True),
    )
    op.drop_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", _FEEDBACK_NEW_CK
    )

    op.add_column(
        "homework_feedback_messages",
        sa.Column("video_url", sa.String(length=500), nullable=True),
    )
    op.drop_constraint(
        "ck_homework_feedback_messages_text_or_photo",
        "homework_feedback_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_homework_feedback_messages_text_or_photo",
        "homework_feedback_messages",
        _HOMEWORK_NEW_CK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_homework_feedback_messages_text_or_photo",
        "homework_feedback_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_homework_feedback_messages_text_or_photo",
        "homework_feedback_messages",
        _HOMEWORK_OLD_CK,
    )
    op.drop_column("homework_feedback_messages", "video_url")

    op.drop_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_feedback_messages_text_or_photo", "feedback_messages", _FEEDBACK_OLD_CK
    )
    op.drop_column("feedback_messages", "video_url")
