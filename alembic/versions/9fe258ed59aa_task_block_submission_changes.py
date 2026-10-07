"""task block submission changes — история правок сданной работы (07.10.2026)

Две новые таблицы: `task_block_submission_changes` — одна правка уже сданной
работы в блоке задания (замена всех фото, удаление одного, догрузка), и
`task_block_submission_change_images` — ссылки на фото, которые правка убрала.
Владелец 07.10.2026: «нужно фиксировать сколько и когда были замены фото в
заданиях»; показывается в карточке проверки.

Прошлое не переносится (развилка, владелец 07.10.2026: «считать с сегодня»):
история начинается с выкатки. Существующие таблицы миграция не трогает.

Откат — снос обеих таблиц; правка сдачи без них работает как раньше.

Revision ID: 9fe258ed59aa
Revises: b123ee32347b
Create Date: 2026-10-07 21:18:29.674818

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9fe258ed59aa'
down_revision: Union[str, None] = 'b123ee32347b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_block_submission_changes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id", sa.Integer(),
            sa.ForeignKey("task_block_submissions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("photos_before", sa.Integer(), nullable=False),
        sa.Column("photos_after", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_task_block_submission_changes_submission_id",
        "task_block_submission_changes", ["submission_id"],
    )
    op.create_table(
        "task_block_submission_change_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "change_id", sa.Integer(),
            sa.ForeignKey("task_block_submission_changes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("image_s3_url", sa.String(length=500), nullable=False),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_task_block_submission_change_images_change_id",
        "task_block_submission_change_images", ["change_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_block_submission_change_images_change_id",
        table_name="task_block_submission_change_images",
    )
    op.drop_table("task_block_submission_change_images")
    op.drop_index(
        "ix_task_block_submission_changes_submission_id",
        table_name="task_block_submission_changes",
    )
    op.drop_table("task_block_submission_changes")
