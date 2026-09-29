"""student_reminders

Отметки «напоминание ученику уже отправлено» (владелец 29.09.2026): новое
задание, новое видео, срок сдачи, конец доступа. Шлёт планировщик
(`app/services/student_reminders.py`), уникальность строки не даёт
повторить сообщение в следующем прогоне.

Revision ID: e7b3d9f5a1c4
Revises: a3d5f7b9c1e2
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7b3d9f5a1c4"
down_revision: Union[str, None] = "a3d5f7b9c1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_reminders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "kind", "ref", name="uq_student_reminders_user_kind_ref",
        ),
    )


def downgrade() -> None:
    op.drop_table("student_reminders")
