"""learning topic cycle period (ends_at)

Владелец 06.09.2026 (голосовое 01:37): «открываем день, устанавливаем, с
какого по какое это будет цикл — то есть он три недели — расставляем блоки
друг за другом». До этой миграции период учебного цикла был всегда ровно
календарной неделей: резолверы в `services/tracker.py` считали понедельник от
`opens_at` и добавляли шесть дней, произвольный период выразить было нечем.

`learning_topics.ends_at` — необязательный конец периода цикла, в UTC (конец
последних суток МСК выбранной даты). `NULL` = прежнее поведение: неделя от
понедельника `opens_at`. Все темы, заведённые до 06.09.2026, остаются с
`NULL` и читаются как раньше — миграция аддитивная, данные не трогает.

Revision ID: 4a1c9e77b210
Revises: 3762fd777389
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a1c9e77b210'
down_revision: Union[str, None] = '3762fd777389'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learning_topics",
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("learning_topics", "ends_at")
