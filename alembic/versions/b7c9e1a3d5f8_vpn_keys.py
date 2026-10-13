"""vpn_keys

13.10.2026, владелец: свой VPN для учеников, ключ выдаёт бот по /vpn
(план 2026-10-13-vpn-amnezia). `vpn_keys` — выданные ключи: пир на сервере
Amnezia (`peer_id`), сам ключ зашифрованным, отзыв (`revoked_at`). Живой ключ
у ученика один — частичный уникальный индекс.

Revision ID: b7c9e1a3d5f8
Revises: a6b8d0f2c4e7
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c9e1a3d5f8"
down_revision: Union[str, None] = "a6b8d0f2c4e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vpn_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("peer_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vpn_keys_user_id", "vpn_keys", ["user_id"])
    op.create_index("ix_vpn_keys_peer_id", "vpn_keys", ["peer_id"], unique=True)
    op.create_index(
        "uq_vpn_keys_live_user", "vpn_keys", ["user_id"], unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_vpn_keys_live_user", table_name="vpn_keys")
    op.drop_index("ix_vpn_keys_peer_id", table_name="vpn_keys")
    op.drop_index("ix_vpn_keys_user_id", table_name="vpn_keys")
    op.drop_table("vpn_keys")
