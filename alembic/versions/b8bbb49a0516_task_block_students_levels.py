"""task_block_students_levels

Кому доступен блок сверх тарифов (владелец 06.10.2026): поимённо по ученику
(«кроме доступа по тарифу будет ещё по юзернейму») и по уровню точки А
(«задание по уровню»). Две таблицы строк, как `task_block_tariffs`. Новые
таблицы пустые — все существующие блоки ведут себя ровно как раньше.

Revision ID: b8bbb49a0516
Revises: a3442221914c
Create Date: 2026-10-06 12:23:51.386075

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8bbb49a0516'
down_revision: Union[str, None] = 'a3442221914c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_block_students",
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_task_block_students_user", "task_block_students", ["user_id"])
    op.create_table(
        "task_block_levels",
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("level", sa.Integer(), primary_key=True),
        sa.CheckConstraint("level IN (1, 2)", name="ck_task_block_levels_level"),
    )


def downgrade() -> None:
    op.drop_table("task_block_levels")
    op.drop_index("ix_task_block_students_user", table_name="task_block_students")
    op.drop_table("task_block_students")
