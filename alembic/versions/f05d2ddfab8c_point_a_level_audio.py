"""точка А: уведомление с голосовым по уровню (1: средний балл ≥70, 2: ≤69)

Владелец 17.09.2026: разворот записи созвона 26.08.2026 («уровень ученику не
сообщается напрямую») — осознанное новое решение, не забытая старая политика.

`point_a_notified_at` на ученике — идемпотентность: уведомили один раз при
переходе всех плашек в «оценено», повторная правка отдельного балла новое
голосовое не шлёт. `audio_url` на уведомлении — только у уведомлений с
голосовым (домашка/пробник это поле не используют). `point_a_level_audios` —
одна запись на уровень (1|2), не на ученика: аудио готовит Главный
преподаватель заранее, перезалив обновляет ту же строку.

Revision ID: f05d2ddfab8c
Revises: 4af427f51e0d
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa


revision = "f05d2ddfab8c"
down_revision = "4af427f51e0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("point_a_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("audio_url", sa.String(length=500), nullable=True),
    )
    op.create_table(
        "point_a_level_audios",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("audio_s3_path", sa.String(length=500), nullable=False),
        sa.Column("audio_s3_url", sa.String(length=500), nullable=False),
        sa.Column(
            "uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("level IN (1, 2)", name="ck_point_a_level_audios_level"),
        sa.UniqueConstraint("level", name="uq_point_a_level_audios_level"),
    )


def downgrade() -> None:
    op.drop_table("point_a_level_audios")
    op.drop_column("notifications", "audio_url")
    op.drop_column("users", "point_a_notified_at")
