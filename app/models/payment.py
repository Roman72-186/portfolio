"""Ежемесячная оплата обучения через Продамус (план
`plans/2026-09-30-apparchi-monthly-payments.md`, начато 07.10.2026).

Две таблицы:

- `payment_prices` — справочник «тариф × набор»: сколько стоит месяц. Набор —
  дата записи до 01.09.2026 или с этой даты (приложение № 2 оферты). Ученик с
  индивидуальной ценой (`User.pay_price_kop`) справочник не читает.
- `payments` — один платёж: за какой месяц, сколько, чем закончился. Строку
  создаёт кнопка «Оплатить» (ссылка Продамуса) или ГП кнопкой «Отметить
  оплату» (перевод на расчётный счёт, п. 3.5 оферты). Сумма фиксируется в
  строке при создании: оплаченный месяц смена тарифа не пересчитывает.

Суммы — в копейках, как везде в проекте; Продамусу уходят рублями.
"""
from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# Набор ученика — какая колонка справочника цен к нему относится.
COHORT_BEFORE = "before_0901"  # записался до 01.09.2026
COHORT_FROM = "from_0901"      # записался с 01.09.2026
COHORTS = (COHORT_BEFORE, COHORT_FROM)
COHORT_LABELS = {
    COHORT_BEFORE: "до 01.09.2026",
    COHORT_FROM: "с 01.09.2026",
}

# Статусы платежа.
STATUS_PENDING = "pending"            # ссылка создана, денег нет
STATUS_PAID = "paid"                  # деньги пришли, доступ продлён
STATUS_CANCELLED = "cancelled"        # ссылку отменили (смена цены, ручная оплата) или ГП снял отметку
STATUS_SUM_MISMATCH = "sum_mismatch"  # пришла не та сумма — доступ не продлеваем, решает человек
STATUS_REFUNDED = "refunded"          # деньги вернули в кабинете Продамуса, доступ закрыт
STATUSES = (STATUS_PENDING, STATUS_PAID, STATUS_CANCELLED, STATUS_SUM_MISMATCH, STATUS_REFUNDED)

# Откуда пришли деньги.
SOURCE_PRODAMUS = "prodamus"
SOURCE_MANUAL = "manual"

# Вид платежа: обычный месяц или разовая доплата разницы при смене тарифа.
KIND_MONTH = "month"
KIND_EXTRA = "extra"


class PaymentPrice(Base):
    __tablename__ = "payment_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tariff: Mapped[str] = mapped_column(String(50), nullable=False)
    cohort: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_kop: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("tariff", "cohort", name="uq_payment_prices_tariff_cohort"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    # Месяц, за который платёж, — первое число этого месяца. Окно оплаты
    # лежит внутри него: октябрь платят 10–15 октября.
    period: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default=KIND_MONTH)
    amount_kop: Mapped[int] = mapped_column(Integer, nullable=False)
    # Тариф на момент создания строки — для экрана «Оплаты» и для разбора
    # «оплатил по старой цене».
    tariff: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_PENDING)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default=SOURCE_PRODAMUS)

    # Ссылка Продамуса и до какого момента она живёт (`link_expired`).
    link_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Номер заказа в Продамусе (`order_id` вебхука). Уникален: повтор
    # вебхука не засчитает оплату второй раз.
    prodamus_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # «Оплачено по» ученика до того, как этот платёж его сдвинул. Отмена
    # ручной отметки и возврат возвращают срок сюда: из одних оставшихся
    # платежей его не восстановить — срок мог стоять из карточки или загрузки.
    paid_until_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Сколько пришло на самом деле — расходится с `amount_kop` у `sum_mismatch`.
    paid_sum_kop: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Номер платёжки, от кого перевод, «по старой цене, разница N ₽».
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    marked_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    refunded_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", foreign_keys=[user_id], lazy="select")

    __table_args__ = (
        Index("ix_payments_user_period", "user_id", "period"),
        Index("ix_payments_period_status", "period", "status"),
    )
