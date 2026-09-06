"""timed block: time limit and start mark

Владелец 03.09.2026: «здесь в контрольной у нас таймер… давай сделаем один
час… мы будем отслеживать статистику, сколько детей превысили время, их можно
будет пометить красненьким».

1. `task_blocks.time_limit_minutes` — лимит работы в минутах, только у блока
   «Работа на время» (`block_type='timed'`). NULL у всех остальных типов.
2. `task_block_states.started_at` — когда ученик нажал «Начать». У остальных
   типов пусто: там строка состояния заводится в момент выполнения, а не
   старта. Превышение лимита считается как `completed_at - started_at`.

Обе колонки добавляются, данные не трогаются.

Revision ID: 7c3f81ab55d2
Revises: 4a1c9e77b210
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c3f81ab55d2'
down_revision: Union[str, None] = '4a1c9e77b210'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_blocks",
        sa.Column("time_limit_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "task_block_states",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_block_states", "started_at")
    op.drop_column("task_blocks", "time_limit_minutes")
