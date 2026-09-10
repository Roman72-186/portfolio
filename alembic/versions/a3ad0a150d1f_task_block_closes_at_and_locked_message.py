"""task block closes_at and locked message

Владелец 10.09.2026 — механика предобучения 17.09-03.10 требует не только
открытие блока по календарю (уже есть, `opens_at`), но и его закрытие:
«27 сентября в 23:30 закрывается доступ к образовательному модулю на
платформе». Плюс свой текст уведомления вместо стандартной фразы ленты,
когда блок заперт по дате («Задание откроется 23 сентября, пока что проверь
чат-комьюнити в телеграмме»).

1. `task_blocks.closes_at` — необязательный момент закрытия, всегда в UTC.
   Несёт время суток, а не только дату (в отличие от `opens_at`, который
   всегда 00:00 МСК) — конвертацию делает `app.services.tz.parse_msk_local`.
2. `task_blocks.locked_message` — текст вместо стандартной фразы «Откроется
   …» / «Доступ закрыт», пусто — рендер ленты решает сам.

Revision ID: a3ad0a150d1f
Revises: 9e2d3c4b5a6f
Create Date: 2026-09-10 09:14:22.896648

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3ad0a150d1f'
down_revision: Union[str, None] = '9e2d3c4b5a6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_blocks",
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_blocks",
        sa.Column("locked_message", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_blocks", "locked_message")
    op.drop_column("task_blocks", "closes_at")
