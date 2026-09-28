"""task block compare steps

Блок «Сравнение работ», уточнение владельца 28.09.2026: выбор в паре
окончательный, прогресс переживает перезагрузку, ход выбора видят проверяющие.
Каждая пара — строка в новой таблице.

Revision ID: b3d5f7a9c1e2
Revises: a7c9e1f3b5d8
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3d5f7a9c1e2"
down_revision: Union[str, None] = "a7c9e1f3b5d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_block_compare_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("left_url", sa.String(500), nullable=False),
        sa.Column("right_url", sa.String(500), nullable=False),
        sa.Column("winner_url", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("block_id", "user_id", "step", name="uq_task_block_compare_step"),
    )
    op.create_index(
        "ix_task_block_compare_steps_block_user",
        "task_block_compare_steps", ["block_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_block_compare_steps_block_user", table_name="task_block_compare_steps")
    op.drop_table("task_block_compare_steps")
