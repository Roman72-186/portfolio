"""task_block_feedback_links

Общая обратная связь на несколько заданий (созвон 13.10.2026, план
`plans/2026-10-14-apparchi-созвон-13-10.md`, часть 2): блок сдачи «главного»
задания связан с блоками сдачи прошлых заданий, куратор отвечает один раз на
все работы ученика по ним.

Связанный блок принадлежит одному главному — уникальный индекс на
`linked_block_id`. Бэкфилла нет: связки ставит преподаватель в конструкторе.

Revision ID: 0ab62c0706c3
Revises: 8495c9449605
Create Date: 2026-10-14 13:49:43.449744

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0ab62c0706c3'
down_revision: Union[str, None] = '8495c9449605'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_block_feedback_links",
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "linked_block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), primary_key=True,
        ),
    )
    op.create_index(
        "ix_task_block_feedback_links_linked_block_id",
        "task_block_feedback_links", ["linked_block_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_block_feedback_links_linked_block_id",
        table_name="task_block_feedback_links",
    )
    op.drop_table("task_block_feedback_links")
