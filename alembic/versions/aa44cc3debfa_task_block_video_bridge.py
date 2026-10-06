"""task_block_video_bridge

Мост до Bunny на уровне видео-блока (владелец 06.10.2026: «добавь в блоке Видео
переключение моста»). Чтобы проверить российскую копию моста на настоящем
задании, видимом только владельцу, не переключая всех учеников. Пусто — блок
идёт по общей настройке `BUNNY_PLAYER_PROXY_BASE`, как все существующие блоки.

Revision ID: aa44cc3debfa
Revises: b8bbb49a0516
Create Date: 2026-10-06 22:45:56.314008

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa44cc3debfa'
down_revision: Union[str, None] = 'b8bbb49a0516'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("task_blocks", sa.Column("video_bridge", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("task_blocks", "video_bridge")
