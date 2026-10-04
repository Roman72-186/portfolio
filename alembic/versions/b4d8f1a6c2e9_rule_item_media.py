"""rule_item_media

Владелец 04.10.2026: в пункт блока «Правила с галочками» можно положить фото,
видео, аудио или текст, а галочка «ознакомлен(а)» стоит под содержимым.

- `task_block_options.content_kind` — что в пункте: photo / video / audio,
  NULL — текст (так выглядят все пункты, заведённые раньше, переносить их не
  нужно);
- `task_block_options.media_s3_url/path` — файл видео или аудио в S3;
- новая таблица `task_block_option_images` — галерея фото-пункта.

Revision ID: b4d8f1a6c2e9
Revises: 0c4e9d2b7a61
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4d8f1a6c2e9"
down_revision: Union[str, None] = "0c4e9d2b7a61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("task_block_options", sa.Column("content_kind", sa.String(10), nullable=True))
    op.add_column("task_block_options", sa.Column("media_s3_url", sa.String(500), nullable=True))
    op.add_column("task_block_options", sa.Column("media_s3_path", sa.String(500), nullable=True))
    op.create_table(
        "task_block_option_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "option_id", sa.Integer(),
            sa.ForeignKey("task_block_options.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("image_s3_url", sa.String(500), nullable=False),
        sa.Column("image_s3_path", sa.String(300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_task_block_option_images_option_id", "task_block_option_images", ["option_id"]
    )
    op.create_index(
        "ix_task_block_option_images_order", "task_block_option_images", ["option_id", "sort_order"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_block_option_images_order", table_name="task_block_option_images")
    op.drop_index("ix_task_block_option_images_option_id", table_name="task_block_option_images")
    op.drop_table("task_block_option_images")
    op.drop_column("task_block_options", "media_s3_path")
    op.drop_column("task_block_options", "media_s3_url")
    op.drop_column("task_block_options", "content_kind")
