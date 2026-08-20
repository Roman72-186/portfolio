"""telegram notifications toggle

Гир-меню рядом с "Выйти" в кабинете ученика (Фаза 4.1): переключатель
Telegram-уведомлений отдельно от Web Push. По умолчанию включён — раньше
Telegram слался безусловно при наличии telegram_chat_id, это сохраняет
прежнее поведение для всех существующих пользователей.

Revision ID: c1a8f5e6b3d2
Revises: a6528d778ba0
Create Date: 2026-08-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a8f5e6b3d2'
down_revision: Union[str, None] = 'a6528d778ba0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("telegram_notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "telegram_notifications_enabled")
