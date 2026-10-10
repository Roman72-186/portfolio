"""user_viewer_owner

Владелец 10.10.2026: в кабинете куратора Алёны — кнопка входа в её аккаунт
просмотра «Уверенного максимума» по паролю, с возвратом обратно.
`users.viewer_owner_id` — куратор, у которого эта кнопка. Пусто — кнопки
нет ни у кого, аккаунт открывается только через `/login`.

Revision ID: d4f8b2a6c1e3
Revises: c3e7a1f9d2b4
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f8b2a6c1e3"
down_revision: Union[str, None] = "c3e7a1f9d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("viewer_owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_viewer_owner_id_users", "users", "users", ["viewer_owner_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_viewer_owner_id_users", "users", type_="foreignkey")
    op.drop_column("users", "viewer_owner_id")
