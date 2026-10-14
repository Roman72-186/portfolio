"""vpn_keys_device

14.10.2026, владелец: ключ VPN — на одно устройство, у ученика до двух
(«ограничить выдачу ключа до 2»). `vpn_keys.device` — номер устройства 1 или 2;
живой ключ уникален по паре (ученик, устройство). Прежние ключи — устройство 1.

Revision ID: 8495c9449605
Revises: b7c9e1a3d5f8
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8495c9449605"
down_revision: Union[str, None] = "b7c9e1a3d5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vpn_keys", sa.Column("device", sa.Integer(), nullable=False, server_default="1"))
    op.drop_index("uq_vpn_keys_live_user", table_name="vpn_keys")
    op.create_index(
        "uq_vpn_keys_live_user_device", "vpn_keys", ["user_id", "device"], unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_vpn_keys_live_user_device", table_name="vpn_keys")
    # Второй живой ключ ученика не уложится в прежний индекс «один на ученика».
    op.execute("UPDATE vpn_keys SET revoked_at = now() WHERE device = 2 AND revoked_at IS NULL")
    op.create_index(
        "uq_vpn_keys_live_user", "vpn_keys", ["user_id"], unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_column("vpn_keys", "device")
