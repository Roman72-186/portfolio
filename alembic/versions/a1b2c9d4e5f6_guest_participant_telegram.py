"""гостевой пробник: вход через Telegram у GuestParticipant

Участник гостевой ссылки теперь входит через Telegram (тот же OIDC-флоу, что и
ученики, но без проверки членства в закрытом канале). Храним chat_id и username,
чтобы узнавать вернувшегося участника с любого устройства.

Уникальность — пара (config_id, telegram_chat_id), а не глобальная: гостевых
ссылок может быть несколько, и весь модуль скоупится по config_id.

Revision ID: a1b2c9d4e5f6
Revises: c7a91f4d2b83
Create Date: 2026-08-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c9d4e5f6'
down_revision: Union[str, None] = 'c7a91f4d2b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'guest_participants',
        sa.Column('telegram_chat_id', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'guest_participants',
        sa.Column('telegram_username', sa.String(), nullable=True),
    )
    op.create_index(
        'ix_guest_participants_telegram_chat_id', 'guest_participants', ['telegram_chat_id']
    )
    op.create_unique_constraint(
        'uq_guest_participant_config_telegram',
        'guest_participants',
        ['config_id', 'telegram_chat_id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_guest_participant_config_telegram', 'guest_participants', type_='unique'
    )
    op.drop_index('ix_guest_participants_telegram_chat_id', table_name='guest_participants')
    op.drop_column('guest_participants', 'telegram_username')
    op.drop_column('guest_participants', 'telegram_chat_id')
