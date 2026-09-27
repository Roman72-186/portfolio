"""task level submit deadlines

Срок сдачи на всё задание плюс свой срок у тарифа (владелец 27.09.2026,
второй заход: «нужно добавить в доступность блока и для всех заданий»).
Блок со своим сроком главнее задания.

Revision ID: e6a8c2d4f5b7
Revises: d5f7b9c1e3a2
Create Date: 2026-09-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6a8c2d4f5b7"
down_revision: Union[str, None] = "d5f7b9c1e3a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Отдельно от `due_at`: тот несёт день, в котором задание лежит на экране
    # календаря, и сроком сдачи быть не может — иначе сдвиг срока переложил бы
    # задание в другой день.
    op.add_column(
        "tracker_tasks",
        sa.Column("submit_until", sa.DateTime(timezone=True), nullable=True),
    )
    # nullable, как у блока: строка с пустым значением означает «у этого
    # тарифа сдача бессрочная», и это не то же самое, что отсутствие строки.
    op.create_table(
        "tracker_task_tariff_deadlines",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("tariff", sa.String(length=50), nullable=False),
        sa.Column("submit_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tracker_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "tariff"),
    )


def downgrade() -> None:
    op.drop_table("tracker_task_tariff_deadlines")
    op.drop_column("tracker_tasks", "submit_until")
