"""learning_topic_tariff_window

Окно доступа к циклу по тарифу (владелец 06.10.2026: «настроить доступность
циклов для тарифов и указать, с какой даты по какое доступен ему данный
цикл»). Две nullable-колонки в строке «тема — тариф»: `opens_at` — с какого
момента тариф видит цикл, `closes_at` — после какого цикл у тарифа уходит в
архив. NULL — без своей границы, как у всех строк до этой миграции: прежние
строки (служебные темы заданий, темы видео) ведут себя ровно как раньше.

Revision ID: a3442221914c
Revises: e4b7c1d9a2f6
Create Date: 2026-10-06 07:29:20.555446

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3442221914c'
down_revision: Union[str, None] = 'e4b7c1d9a2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learning_topic_tariffs",
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "learning_topic_tariffs",
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("learning_topic_tariffs", "closes_at")
    op.drop_column("learning_topic_tariffs", "opens_at")
