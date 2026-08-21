"""video view log — счётчик возвратов к видео

Отдельная insert-only таблица, не колонка поверх video_progress: та таблица
хранит текущую позицию просмотра (одна строка на пару ученик×видео), эта —
историю каждого открытия плеера, откуда считается «сколько раз возвращался»
и «когда именно».

Revision ID: 1f6315c0983e
Revises: c8e1a4f37b02
Create Date: 2026-08-21

Ветвится не от b7f21c93ad40, а от c8e1a4f37b02: параллельная сессия завела
свою миграцию на той же голове раньше, чем эта была дописана (см. AGENTS.md,
«рядом может идти вторая сессия» — их файл не трогаем, только перецепляем
свою цепочку после него, чтобы `alembic heads` осталась одной головой).
"""
from alembic import op
import sqlalchemy as sa


revision = "1f6315c0983e"
down_revision = "c8e1a4f37b02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_view_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_video_view_logs_user_video", "video_view_logs", ["user_id", "video_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_video_view_logs_user_video", table_name="video_view_logs")
    op.drop_table("video_view_logs")
