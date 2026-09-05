"""task block access period and sequence bypass

Владелец 06.09.2026 — повторный разбор созвона 03.09 нашёл механику,
которую первая реализация (7982848a4a01) пропустила: блок открывается по
календарной дате/времени независимо от того, выполнил ли ученик
предыдущий блок («теория и задания откроются только с 23 сентября 0000»).
Заодно найден конфликт с уже задеплоенным поведением: ссылка (BLOCK_LINK)
была жёстко зашита всегда обходить последовательную блокировку, а созвон
03.09 явно требует обратного для тарифа «Уверенный максимум» — та же
ссылка должна ждать сдачи домашки. Жёсткая привязка к типу блока заменена
явным флагом, который куратор проставляет сам.

1. `task_blocks.opens_at` — необязательная дата/время открытия, всегда в
   UTC (полночь МСК выбранной даты). Складывается с `is_required`, не
   заменяет: блок ждёт оба условия.
2. `task_blocks.bypass_sequence` — явное разрешение обойти последовательную
   блокировку. default=False: без него ничего не меняется для уже
   созданных блоков, включая уже существующие BLOCK_LINK на проде.

Revision ID: 3762fd777389
Revises: 7982848a4a01
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3762fd777389'
down_revision: Union[str, None] = '7982848a4a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_blocks",
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_blocks",
        sa.Column(
            "bypass_sequence", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("task_blocks", "bypass_sequence")
    op.drop_column("task_blocks", "opens_at")
