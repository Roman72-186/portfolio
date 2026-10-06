"""drop_task_block_video_bridge

Переключатель моста у видео-блока снят (владелец 06.10.2026: «про мост в задании
Видео убери вкладку»). Он был нужен один вечер — проверить российскую копию моста
на задании, открытом только владельцу; после проверки на российский мост
переведены все ученики общей настройкой `BUNNY_PLAYER_PROXY_BASE`. Поле удаляется
целиком, иначе у проверочного блока осталось бы невидимое значение, которое
из конструктора уже не поменять.

Revision ID: 24a3a585a53b
Revises: aa44cc3debfa
Create Date: 2026-10-06 23:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '24a3a585a53b'
down_revision: Union[str, None] = 'aa44cc3debfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("task_blocks", "video_bridge")


def downgrade() -> None:
    op.add_column("task_blocks", sa.Column("video_bridge", sa.String(length=10), nullable=True))
