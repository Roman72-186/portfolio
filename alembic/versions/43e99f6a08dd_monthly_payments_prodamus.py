"""monthly payments prodamus — ежемесячная оплата через Продамус (07.10.2026)

План — `plans/2026-09-30-apparchi-monthly-payments.md`.

- `payment_prices` — справочник «тариф × набор» с ценами приложения № 2
  оферты (сверено 30.09.2026). Таблица от заказчика сверяется с ним, правка —
  обновлением строк, не кода.
- `payments` — платежи: месяц, сумма, статус, ссылка Продамуса, номер заказа.
- `users`: окно оплаты (`pay_window_start/end`), набор (`pay_cohort`),
  индивидуальная цена (`pay_price_kop`), «оплачено по» (`paid_until`).
  Все пустые: до импорта списка от заказчика никто не в автоматической
  оплате, и кабинет никому не закрывается.

Откат — снос двух таблиц и пяти колонок; данные оплат при этом теряются.

Revision ID: 43e99f6a08dd
Revises: 9fe258ed59aa
Create Date: 2026-10-07 21:39:00.036996

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '43e99f6a08dd'
down_revision: Union[str, None] = '9fe258ed59aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Приложение № 2 оферты, копейки: (тариф, набор, цена).
OFFER_PRICES = [
    ("Я САМ", "before_0901", 677500),
    ("Я САМ", "from_0901", 677500),
    ("Я С ВАМИ", "before_0901", 1275500),
    ("Я С ВАМИ", "from_0901", 1325500),
    ("УВЕРЕННЫЙ МАКСИМУМ", "before_0901", 1875500),
    ("УВЕРЕННЫЙ МАКСИМУМ", "from_0901", 1925500),
]


def upgrade() -> None:
    prices = op.create_table(
        "payment_prices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tariff", sa.String(length=50), nullable=False),
        sa.Column("cohort", sa.String(length=20), nullable=False),
        sa.Column("amount_kop", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tariff", "cohort", name="uq_payment_prices_tariff_cohort"),
    )
    op.bulk_insert(prices, [
        {"tariff": tariff, "cohort": cohort, "amount_kop": amount}
        for tariff, cohort, amount in OFFER_PRICES
    ])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False, server_default="month"),
        sa.Column("amount_kop", sa.Integer(), nullable=False),
        sa.Column("tariff", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="prodamus"),
        sa.Column("link_url", sa.String(length=500), nullable=True),
        sa.Column("link_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prodamus_order_id", sa.String(length=50), nullable=True, unique=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_sum_kop", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("marked_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("refunded_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_payments_user_period", "payments", ["user_id", "period"])
    op.create_index("ix_payments_period_status", "payments", ["period", "status"])

    op.add_column("users", sa.Column("pay_window_start", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("pay_window_end", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column(
        "pay_cohort", sa.String(length=20), nullable=False, server_default=""))
    op.add_column("users", sa.Column("pay_price_kop", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_paid_until", "users", ["paid_until"])


def downgrade() -> None:
    op.drop_index("ix_users_paid_until", table_name="users")
    op.drop_column("users", "paid_until")
    op.drop_column("users", "pay_price_kop")
    op.drop_column("users", "pay_cohort")
    op.drop_column("users", "pay_window_end")
    op.drop_column("users", "pay_window_start")
    op.drop_index("ix_payments_period_status", table_name="payments")
    op.drop_index("ix_payments_user_period", table_name="payments")
    op.drop_table("payments")
    op.drop_table("payment_prices")
