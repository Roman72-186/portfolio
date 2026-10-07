"""Экран «Оплаты»: кто за месяц оплатил, кто нет (шаг 3 плана оплаты).

План — `plans/2026-09-30-apparchi-monthly-payments.md`. Заказчик: «экран кто
оплатил / кто нет, как статистика просмотров видео».

Оплачен ли месяц — по «оплачено по» ученика (`payments.paid_month`), а не по
строкам платежей: срок мог поставить человек в карточке или загрузкой
списка, и платежа за такой месяц нет. Строка платежа, если есть, даёт
подробности — сумму, дату, Продамус или вручную, кто отметил.

Кто на экране — те же, кто в учёте: ученики вне архива, не заблокированные,
без служебных (`REPORT_EXCLUDED_USER_IDS`). Ученики без окна оплаты стоят
отдельно: они вне автоматической оплаты, и долга у них не бывает.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session as DBSession

from app.constants import REPORT_EXCLUDED_USER_IDS, TARIFF_DISPLAY
from app.models.payment import (
    KIND_MONTH,
    SOURCE_MANUAL,
    STATUS_PAID,
    STATUS_REFUNDED,
    STATUS_SUM_MISMATCH,
    Payment,
)
from app.models.role import Role
from app.models.user import User
from app.services import payments
from app.services.tz import MSK_TZ

# Состояние ученика за месяц; порядок — порядок строк на экране.
STATE_OVERDUE = "overdue"    # окно закрылось, оплаты нет
STATE_OPEN = "open"          # окно идёт
STATE_UPCOMING = "upcoming"  # окно ещё не открылось
STATE_PAID = "paid"
STATE_ORDER = (STATE_OVERDUE, STATE_OPEN, STATE_UPCOMING, STATE_PAID)


@dataclass
class BoardRow:
    student: User
    state: str
    window_text: str
    amount_kop: int | None          # оплачено или сколько ждём
    payment: Payment | None = None  # оплаченный платёж, если есть
    detail: str = ""                # как оплачено или до какого числа окно
    problems: list[str] = field(default_factory=list)


@dataclass
class Board:
    period: date
    rows: list[BoardRow]
    no_window: list[User]
    counts: dict[str, int]
    paid_total_kop: int


def parse_month(raw: str | None, now: datetime) -> date:
    """«2026-10» → 1 октября 2026; пусто или мусор — текущий месяц по Москве."""
    try:
        parsed = payments.parse_paid_month(raw)
    except payments.PaymentSettingsError:
        parsed = None
    return parsed or payments.month_start(now.astimezone(MSK_TZ).date())


def _students(db: DBSession) -> list[User]:
    return (
        db.query(User)
        .join(Role, Role.id == User.role_id)
        .filter(
            Role.rank == 1,
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
            User.is_active == True,  # noqa: E712
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),
        )
        .order_by(User.last_name, User.first_name, User.id)
        .all()
    )


def display_name(student: User) -> str:
    return f"{student.last_name or ''} {student.first_name or student.name}".strip()


def tariff_text(code: str | None) -> str:
    return TARIFF_DISPLAY.get(code or "", code or "–")


def _msk_day(value: datetime) -> str:
    return value.astimezone(MSK_TZ).strftime("%d.%m")


def _paid_detail(db: DBSession, payment: Payment | None) -> str:
    if payment is None:
        return "Отмечено в настройках оплаты"
    when = payment.paid_at.astimezone(MSK_TZ).strftime("%d.%m.%Y") if payment.paid_at else ""
    if payment.source == SOURCE_MANUAL:
        who = db.get(User, payment.marked_by_id) if payment.marked_by_id else None
        by = f", {display_name(who)}" if who else ""
        return f"Вручную{by}, {when}"
    return f"Продамус, {when}"


def month_board(db: DBSession, period: date, now: datetime) -> Board:
    students = _students(db)
    by_user: dict[int, list[Payment]] = {}
    for payment in (
        db.query(Payment)
        .filter(Payment.period == period, Payment.kind == KIND_MONTH,
                Payment.user_id.in_([s.id for s in students] or [0]))
        .order_by(Payment.id)
    ):
        by_user.setdefault(payment.user_id, []).append(payment)

    rows: list[BoardRow] = []
    no_window: list[User] = []
    paid_total = 0
    for student in students:
        if not payments.has_window(student):
            no_window.append(student)
            continue
        own = by_user.get(student.id, [])
        paid = next((p for p in own if p.status == STATUS_PAID), None)
        problems = []
        for p in own:
            if p.status == STATUS_SUM_MISMATCH:
                problems.append(f"Пришла не та сумма: {payments.rub_text(p.paid_sum_kop or 0)} вместо {payments.rub_text(p.amount_kop)}")
            elif p.status == STATUS_REFUNDED:
                problems.append(f"Возврат {payments.rub_text(p.paid_sum_kop or p.amount_kop)}")

        paid_month = payments.paid_month(student)
        window = f"{student.pay_window_start}–{student.pay_window_end}"
        if paid is not None or (paid_month is not None and paid_month >= period):
            amount = (paid.paid_sum_kop or paid.amount_kop) if paid else None
            paid_total += amount or 0
            rows.append(BoardRow(student, STATE_PAID, window, amount, paid, _paid_detail(db, paid), problems))
            continue

        opens = payments.window_opens_at(student, period)
        cutoff = payments.window_cutoff(student, period)
        if now < opens:
            state, detail = STATE_UPCOMING, f"Окно откроется {_msk_day(opens)}"
        elif now < cutoff:
            state, detail = STATE_OPEN, f"Окно до {student.pay_window_end:02d}.{period.month:02d}"
        else:
            state, detail = STATE_OVERDUE, f"Окно закрылось {_msk_day(cutoff)}"
        rows.append(BoardRow(student, state, window, payments.price_kop(db, student), None, detail, problems))

    rows.sort(key=lambda r: STATE_ORDER.index(r.state))
    counts = {state: sum(1 for r in rows if r.state == state) for state in STATE_ORDER}
    return Board(period, rows, no_window, counts, paid_total)
