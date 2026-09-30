"""task_blocks_poll

Опрос — несколько вопросов в одном блоке конструктора (владелец 30.09.2026,
созвон: «заголовок, описание, и пошли вопросы… кнопку далее, и всё это в
одном блоке»). В базе опрос — подряд идущие блоки `question`/`scale` одного
задания с общим `poll_key`; описание опроса лежит на первом из них.

Колонки только добавляются и nullable — старые блоки остаются как были,
бэкфилл не нужен: одиночный вопрос без ключа конструктор сам показывает
опросом из одного вопроса и выдаёт ключ при следующем сохранении.

Revision ID: 9f1ade97eedb
Revises: b8e4c2a6d0f3
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9f1ade97eedb'
down_revision: Union[str, None] = 'b8e4c2a6d0f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('task_blocks', sa.Column('poll_key', sa.String(32), nullable=True))
    op.add_column('task_blocks', sa.Column('poll_intro', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('task_blocks', 'poll_intro')
    op.drop_column('task_blocks', 'poll_key')
