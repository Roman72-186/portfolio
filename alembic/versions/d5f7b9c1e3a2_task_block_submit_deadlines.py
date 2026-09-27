"""task block submit deadlines

Срок приёма работ у блока сдачи плюс свой срок у отдельного тарифа
(владелец 27.09.2026, после третьего цикла: работы правили и догружали
после дедлайна, потому что приём никто не закрывал).

Revision ID: d5f7b9c1e3a2
Revises: c4e6a8b0d2f1
Create Date: 2026-09-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5f7b9c1e3a2"
down_revision: Union[str, None] = "c4e6a8b0d2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Отдельная колонка, а не новый смысл у `closes_at`: тот закрывает блок
    # целиком и держит закрытие модуля предобучения и окно портфолио.
    # Пусто у всех существующих блоков — приём как раньше без ограничения,
    # поведение прода не меняется до первой простановки срока.
    op.add_column(
        "task_blocks",
        sa.Column("submit_until", sa.DateTime(timezone=True), nullable=True),
    )
    # `submit_until` тут nullable намеренно: строка с пустым значением означает
    # «у этого тарифа приём бессрочный», отсутствие строки — «как у всех».
    op.create_table(
        "task_block_tariff_deadlines",
        sa.Column("block_id", sa.Integer(), nullable=False),
        sa.Column("tariff", sa.String(length=50), nullable=False),
        sa.Column("submit_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["block_id"], ["task_blocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("block_id", "tariff"),
    )


def downgrade() -> None:
    op.drop_table("task_block_tariff_deadlines")
    op.drop_column("task_blocks", "submit_until")
