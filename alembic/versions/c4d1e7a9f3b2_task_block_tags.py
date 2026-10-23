"""task_block_tags

Кому доступен блок по треку каникулярного набора (владелец 23.10.2026:
«доступность блока нужно настроить под каникулярные теги»). Трек ученика —
его тег, тарифа у каникулярного нет: делить блоки одного цикла между
«1 треком» и «2 треком» было нечем. Таблица строк, как `task_block_students`.
Новая таблица пустая — все существующие блоки ведут себя ровно как раньше.

Revision ID: c4d1e7a9f3b2
Revises: 8deead07634a
Create Date: 2026-10-23 20:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d1e7a9f3b2'
down_revision: Union[str, None] = '8deead07634a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_block_tags",
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id", sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("task_block_tags")
