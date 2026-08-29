"""learning_videos: auto_publish_on_ready — куратор ставит ролик в день, публикация не ждёт отдельного клика

Revision ID: 055b836671da
Revises: a1b2c9d4e5f6
Create Date: 2026-08-29 13:31:16.878611

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '055b836671da'
down_revision: Union[str, None] = 'a1b2c9d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learning_videos",
        sa.Column("auto_publish_on_ready", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("learning_videos", "auto_publish_on_ready")
