"""task_block_feedback_messages.is_draft — ОС уходит ученику по кнопке (22.10.2026)

Служба заботы 21.10.2026: на одну ОС ученику приходило 5–15 одинаковых
уведомлений — по одному на каждое сообщение сотрудника. Владелец 22.10.2026:
сообщения сотрудника копятся черновиком, ученику их открывает и одним
уведомлением сообщает кнопка «Отправить ОС».

`is_draft` с умолчанием `false`: все прежние сообщения ученик уже видел,
перекладывать нечего. `sent_at` — когда ученик получил сообщение: от него
считаются двое суток тишины, после которых можно «Завершить ОС»; у прежних
сообщений пусто, вместо него берётся `created_at`. Откат: колонки удаляются,
неотправленные черновики станут видны ученику без уведомления.

Revision ID: a7c3e9f1b2d4
Revises: f2b6d8a1c3e5
Create Date: 2026-10-22 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b2d4'
down_revision: Union[str, None] = 'f2b6d8a1c3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_block_feedback_messages",
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "task_block_feedback_messages",
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_block_feedback_messages", "sent_at")
    op.drop_column("task_block_feedback_messages", "is_draft")
