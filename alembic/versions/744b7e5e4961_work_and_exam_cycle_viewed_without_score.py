"""work and exam_cycle viewed without score

Решение владельца 01.09.2026 (вопрос 4,
plans/2026-09-01-apparchi-student-centric-review.md): «просмотрено без
оценки» у `TaskBlockAnswer` уже есть — расширяем тот же паттерн на `Work` и
`ExamCycle`, у которых сейчас есть только балл/закрытие, но не отдельная
отметка просмотра. Нужна единому экрану проверки (этап 3 плана): куратор
может отметить, что посмотрел работу, не выставляя балл прямо сейчас.

Revision ID: 744b7e5e4961
Revises: 7b2f4c81ea59
Create Date: 2026-09-01 19:19:02.701064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '744b7e5e4961'
down_revision: Union[str, None] = '7b2f4c81ea59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "works",
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "works",
        sa.Column(
            "viewed_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.add_column(
        "exam_cycles",
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "exam_cycles",
        sa.Column(
            "viewed_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("exam_cycles", "viewed_by_id")
    op.drop_column("exam_cycles", "viewed_at")
    op.drop_column("works", "viewed_by_id")
    op.drop_column("works", "viewed_at")
