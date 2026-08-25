"""архив учеников: users.archived_at

Архив — не удаление и не блокировка. Ученик прошлого потока уходит из рабочих
списков (is_active=False), но его работы, оценки и переписки остаются целыми и
видны суперадмину. Отдельное поле нужно потому, что deleted_at сбрасывается при
входе (auth._upsert_user / _upsert_telegram_user снимают soft-delete), а архив
должен пережить любой вход.

Revision ID: c7a91f4d2b83
Revises: f67ad003ad97
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7a91f4d2b83'
down_revision: Union[str, None] = 'f67ad003ad97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_users_archived_at', 'users', ['archived_at'])


def downgrade() -> None:
    op.drop_index('ix_users_archived_at', table_name='users')
    op.drop_column('users', 'archived_at')
