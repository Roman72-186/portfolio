"""Описание навыка и подписи краёв шкалы у варианта блока.

Анкета «Метакомпетенции» (владелец 11.09.2026): у каждого навыка в блоке
«Шкала навыков» появляется своё описание и пояснения к краям шкалы 0 и 10.
Три nullable-колонки, используются только у BLOCK_SCALE (см. докстринг
TaskBlockOption в app/models/task_block.py) — у вопросов и правил остаются
NULL.

Revision ID: 88aa51ea931b
Revises: 018aba8e6d0f
Create Date: 2026-09-12 00:07:46.724402

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '88aa51ea931b'
down_revision: Union[str, None] = '018aba8e6d0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("task_block_options", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "task_block_options",
        sa.Column("scale_min_label", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "task_block_options",
        sa.Column("scale_max_label", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_block_options", "scale_max_label")
    op.drop_column("task_block_options", "scale_min_label")
    op.drop_column("task_block_options", "description")
