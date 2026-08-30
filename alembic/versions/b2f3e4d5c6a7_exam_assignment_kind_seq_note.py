"""exam_assignments: kind / seq_number / note

Revision ID: b2f3e4d5c6a7
Revises: a1f2e3d4c5b6
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa


revision = "b2f3e4d5c6a7"
down_revision = "a1f2e3d4c5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `exam_assignments` не заводится ни одной миграцией — тот же пробел,
    # что у exam_tickets/mock_exam_attempts/users.parent_phone, найдено
    # 30.08.2026 при поднятии чистого docker-compose. На проде уже есть —
    # no-op, на свежей базе — минимальный стаб.
    op.execute("CREATE TABLE IF NOT EXISTS exam_assignments (id SERIAL PRIMARY KEY)")

    # kind: тип задания — "mock" (Пробник) | "control" (Контрольная).
    # server_default гарантирует валидное значение для существующих строк.
    op.add_column(
        "exam_assignments",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="mock",
        ),
    )
    # seq_number: сквозной номер в пределах (kind, subject). NULL у legacy-заданий.
    op.add_column(
        "exam_assignments",
        sa.Column("seq_number", sa.Integer(), nullable=True),
    )
    # note: необязательный подзаголовок/примечание.
    op.add_column(
        "exam_assignments",
        sa.Column("note", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("exam_assignments", "note")
    op.drop_column("exam_assignments", "seq_number")
    op.drop_column("exam_assignments", "kind")
