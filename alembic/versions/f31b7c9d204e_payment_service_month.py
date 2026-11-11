"""Translate legacy 25–30 periods to service months, preserving access dates.

Approved 11.11.2026: existing next-payment dates must not move.
Payment IDs, money, statuses, signed URLs and paid_until snapshots stay intact.
"""
from datetime import date

from alembic import op
import sqlalchemy as sa

revision = "f31b7c9d204e"
down_revision = "c82e1d6b904a"
branch_labels = None
depends_on = None

MARKER = "[calendar-25-30-v2]"


def upgrade():
    conn = op.get_bind()
    users = sa.table("users", sa.column("id"), sa.column("pay_window_start"), sa.column("pay_window_end"))
    payments = sa.table("payments", sa.column("id"), sa.column("user_id"),
                        sa.column("kind"), sa.column("period", sa.Date), sa.column("note", sa.Text))
    rows = conn.execute(sa.select(payments.c.id, payments.c.period, payments.c.note).join(
        users, users.c.id == payments.c.user_id).where(
        users.c.pay_window_start == 25, users.c.pay_window_end == 30,
        payments.c.kind.in_(["month", "extra"]))).all()
    for row in rows:
        if MARKER in (row.note or ""):
            continue
        old = row.period
        new = date(old.year + (old.month == 12), old.month % 12 + 1, 1)
        note = ((row.note + "\n") if row.note else "") + f"{MARKER} Месяц обучения уточнён: {old:%Y-%m} → {new:%Y-%m}. Сумма и срок доступа сохранены."
        conn.execute(payments.update().where(payments.c.id == row.id).values(period=new, note=note))


def downgrade():
    # После новых оплат автоматическое возвращение старой календарной модели
    # может менять уже оплаченный доступ. Откат требует сверки и снимка базы.
    raise RuntimeError("Calendar migration requires a reviewed data rollback; do not reinterpret new payments automatically")
