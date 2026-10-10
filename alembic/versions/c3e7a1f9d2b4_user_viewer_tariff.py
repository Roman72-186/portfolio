"""user_viewer_tariff

Владелец 10.10.2026: куратору Алёне нужен отдельный вход по паролю, где
видны все ученики «Уверенного максимума», а не только её четверо. Аккаунт —
модератор (ничего не меняет), поле `users.viewer_tariff` сужает его до
одного тарифа. Пусто — обычный модератор, у всех существующих строк так и
остаётся.

Revision ID: c3e7a1f9d2b4
Revises: a99f47892e6b
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e7a1f9d2b4"
down_revision: Union[str, None] = "a99f47892e6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("viewer_tariff", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "viewer_tariff")
