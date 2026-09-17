"""Видео-файл и голосовое — во всех диалогах ОС, не только у пробника

Владелец 17.09.2026: у обратной связи по домашке, по блокам задания и у
гостевой сдачи должны быть те же вложения, что в эталонном диалоге
Feedback/FeedbackMessage (пробник/портфолио) — видео-файл и голосовое, а не
только фото и ссылка на видео.

- `homework_feedback_messages` — колонки video_s3_path/url уже были
  (заведены, но не заполнялись), добавляются только audio_s3_path/url.
- `task_block_feedback_messages` — ни видео-файла, ни голосового не было
  вообще, добавляются все четыре колонки.
- `guest_submissions` — добавляются feedback_video_url/path и
  feedback_audio_url/path (по аналогии с уже существующим
  feedback_image_url/path).

Revision ID: a7cdccec5065
Revises: 9b47c521ad31
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa


revision = "a7cdccec5065"
down_revision = "9b47c521ad31"
branch_labels = None
depends_on = None

_HOMEWORK_OLD_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL) "
    "OR (video_url IS NOT NULL)"
)
_HOMEWORK_NEW_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL) "
    "OR (video_url IS NOT NULL) OR (audio_s3_url IS NOT NULL)"
)

_TASK_BLOCK_OLD_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_url IS NOT NULL)"
)
_TASK_BLOCK_NEW_CK = (
    "(text IS NOT NULL AND length(text) > 0) "
    "OR (photo_s3_url IS NOT NULL) OR (video_url IS NOT NULL) "
    "OR (video_s3_url IS NOT NULL) OR (audio_s3_url IS NOT NULL)"
)


def upgrade() -> None:
    # ── Домашка: голосовое (видео-колонки уже существовали) ─────────────────
    op.add_column(
        "homework_feedback_messages",
        sa.Column("audio_s3_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "homework_feedback_messages",
        sa.Column("audio_s3_url", sa.String(length=500), nullable=True),
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

    # ── Блоки задания: видео-файл и голосовое (не было ни одного) ───────────
    op.add_column(
        "task_block_feedback_messages",
        sa.Column("video_s3_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "task_block_feedback_messages",
        sa.Column("video_s3_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "task_block_feedback_messages",
        sa.Column("audio_s3_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "task_block_feedback_messages",
        sa.Column("audio_s3_url", sa.String(length=500), nullable=True),
    )
    op.drop_constraint(
        "ck_task_block_feedback_messages_content",
        "task_block_feedback_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_task_block_feedback_messages_content",
        "task_block_feedback_messages",
        _TASK_BLOCK_NEW_CK,
    )

    # ── Гостевая сдача: видео и голосовое обратной связи ─────────────────────
    op.add_column(
        "guest_submissions",
        sa.Column("feedback_video_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "guest_submissions",
        sa.Column("feedback_video_path", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "guest_submissions",
        sa.Column("feedback_audio_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "guest_submissions",
        sa.Column("feedback_audio_path", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("guest_submissions", "feedback_audio_path")
    op.drop_column("guest_submissions", "feedback_audio_url")
    op.drop_column("guest_submissions", "feedback_video_path")
    op.drop_column("guest_submissions", "feedback_video_url")

    op.drop_constraint(
        "ck_task_block_feedback_messages_content",
        "task_block_feedback_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_task_block_feedback_messages_content",
        "task_block_feedback_messages",
        _TASK_BLOCK_OLD_CK,
    )
    op.drop_column("task_block_feedback_messages", "audio_s3_url")
    op.drop_column("task_block_feedback_messages", "audio_s3_path")
    op.drop_column("task_block_feedback_messages", "video_s3_url")
    op.drop_column("task_block_feedback_messages", "video_s3_path")

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
    op.drop_column("homework_feedback_messages", "audio_s3_url")
    op.drop_column("homework_feedback_messages", "audio_s3_path")
