"""feedback message edit

Правка отправленной ОС (владелец 05.10.2026).

- `edited_at` у сообщений обоих диалогов ОС — пробника (`feedback_messages`)
  и сдачи в задании (`task_block_feedback_messages`): пометка «изменено»,
  чтобы правка не проходила незаметно для ученика и команды.
- `revision_requested_at` / `revision_done_at` у диалога сдачи в задании
  (`task_block_feedbacks`): ГП или суперадмин вернул завершённую ОС автору на
  правку — та же пара, что у `exam_cycles` в пробнике.

Все поля необязательные, миграция ничего не заполняет: старые сообщения
не правились, старые диалоги на правку не возвращались.

Revision ID: b3e1c7d2a9f4
Revises: a206e833f766
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3e1c7d2a9f4"
down_revision: Union[str, None] = "a206e833f766"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feedback_messages",
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_block_feedback_messages",
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_block_feedbacks",
        sa.Column("revision_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_block_feedbacks",
        sa.Column("revision_done_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("task_block_feedbacks") as batch:
        batch.drop_column("revision_done_at")
        batch.drop_column("revision_requested_at")
    with op.batch_alter_table("task_block_feedback_messages") as batch:
        batch.drop_column("edited_at")
    with op.batch_alter_table("feedback_messages") as batch:
        batch.drop_column("edited_at")
