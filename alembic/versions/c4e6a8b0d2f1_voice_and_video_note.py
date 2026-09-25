"""voice and video note (голосовое и кружок преподавателя)

Владелец 25.09.2026: «чтобы можно было записать голосовое и даже кружок как
в тг». Две части:

1. `video_is_note` в трёх таблицах сообщений переписки по работе
   (`feedback_messages`, `homework_feedback_messages`,
   `task_block_feedback_messages`). Файл кружка лежит в той же колонке
   `video_s3_url`, флаг меняет только отрисовку. `server_default false` —
   старые сообщения остаются обычным видео, backfill не нужен.
2. Медиа-колонки `task_blocks` под новый тип блока `media`: вид
   (`voice`/`note`), публичный URL и путь в S3. Все nullable — у остальных
   типов блоков они пустые.

Revision ID: c4e6a8b0d2f1
Revises: b3f5d8c1a204
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e6a8b0d2f1'
down_revision: Union[str, None] = 'b3f5d8c1a204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MESSAGE_TABLES = (
    "feedback_messages",
    "homework_feedback_messages",
    "task_block_feedback_messages",
)


def upgrade() -> None:
    for table in MESSAGE_TABLES:
        op.add_column(
            table,
            sa.Column(
                "video_is_note", sa.Boolean(), nullable=False,
                server_default=sa.false(),
            ),
        )
    op.add_column("task_blocks", sa.Column("media_kind", sa.String(10), nullable=True))
    op.add_column("task_blocks", sa.Column("media_s3_url", sa.String(500), nullable=True))
    op.add_column("task_blocks", sa.Column("media_s3_path", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("task_blocks", "media_s3_path")
    op.drop_column("task_blocks", "media_s3_url")
    op.drop_column("task_blocks", "media_kind")
    for table in MESSAGE_TABLES:
        op.drop_column(table, "video_is_note")
