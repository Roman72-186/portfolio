"""task block image is_pick

Блок «Сравнение работ» (Лиза, голосовое 27.09.2026): преподаватель отмечает
одну работу из галереи блока как свой выбор, ученик потом сверяется с ней.

Revision ID: a7c9e1f3b5d8
Revises: e6a8c2d4f5b7
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c9e1f3b5d8"
down_revision: Union[str, None] = "e6a8c2d4f5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default: у картинок уже сохранённых блоков-галерей отметки нет.
    op.add_column(
        "task_block_images",
        sa.Column("is_pick", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("task_block_images", "is_pick")
