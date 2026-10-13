"""user_pay_next_tariff

13.10.2026, владелец: ученик, сменивший тариф до оплаты, платит уже по
новому, а тариф на платформе переключается в момент оплаты — не раньше
(тариф сразу меняет доступ к материалам). `users.pay_next_tariff` — тариф
с ближайшей оплаты; пусто — не меняется.

Revision ID: a6b8d0f2c4e7
Revises: e5a7c9b1d3f6
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6b8d0f2c4e7"
down_revision: Union[str, None] = "e5a7c9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("pay_next_tariff", sa.String(50), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("users", "pay_next_tariff")
