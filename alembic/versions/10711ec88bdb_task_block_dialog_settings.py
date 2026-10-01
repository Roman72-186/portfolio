"""task_block_dialog_settings

«Настройка диалога» у блока сдачи (созвон 30.09.2026, правила подтверждены
владельцем 01.10.2026, план `plans/2026-10-01-apparchi-call-30-09-followup.md`,
вопросы О1–О26): кто из учеников может ответить на обратную связь куратора и
сколько сообщений.

- `task_block_dialog_tariffs` — тарифы, которым разрешён ответ. **Пусто =
  ответ закрыт всем**, обратно `task_block_tariffs`, где пусто = всем видно.
- `task_blocks.dialog_reply_limit` — сколько сообщений может отправить
  ученик в диалоге одной сдачи.

Бэкфилла нет намеренно: у всех существующих блоков ответ становится закрытым
(О9 — «закрыть ответ и в старых»), команда открывает нужные в конструкторе.

Revision ID: 10711ec88bdb
Revises: f6fe35a66712
Create Date: 2026-10-01 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '10711ec88bdb'
down_revision: Union[str, None] = 'f6fe35a66712'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_blocks",
        sa.Column("dialog_reply_limit", sa.Integer(), nullable=True),
    )
    op.create_table(
        "task_block_dialog_tariffs",
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("tariff", sa.String(length=50), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("task_block_dialog_tariffs")
    op.drop_column("task_blocks", "dialog_reply_limit")
