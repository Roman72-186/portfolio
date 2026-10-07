"""Ежемесячная оплата обучения через Продамус.

План — `plans/2026-09-30-apparchi-monthly-payments.md`, инструкция заказчику —
`docs/инструкция-продамус.md`. Начато 07.10.2026.

Как устроено:

- У ученика своё **окно оплаты** — дни месяца «с» и «по» (`User.pay_window_*`).
  Месяц платят внутри его окна: октябрь — 10–15 октября.
- **Отсечка** — утро после последнего дня окна (`CUTOFF_HOUR_MSK`). Не оплатил
  к отсечке — при включённой блокировке кабинет закрывается
  (`services/access_state.py`).
- `User.paid_until` — отсечка окна **следующего** неоплаченного месяца.
  Оплатил октябрь — `paid_until` = отсечка ноябрьского окна. Отсюда же
  следует, какой месяц ученик должен сейчас (`due_period`).
- **Ссылка разовая на каждый месяц**, не подписка: сумма — цена ученика в
  момент нажатия «Оплатить», фиксируется в строке `Payment`. Сменили тариф —
  следующая ссылка уже с новой ценой.
- **Одна точка зачисления** — `record_paid`: её зовут и вебхук Продамуса, и
  ручная отметка ГП, чтобы автоматическая и ручная оплата не разошлись.

Подделать оплату нельзя: вебхук проверяется подписью секретным ключом, а сумма
сверяется со строкой платежа, созданной платформой.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.constants import PAYMENT_NOTIFY_RECIPIENT_IDS, TARIFF_DISPLAY
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.payment import (
    KIND_MONTH,
    SOURCE_MANUAL,
    SOURCE_PRODAMUS,
    STATUS_CANCELLED,
    STATUS_PAID,
    STATUS_PENDING,
    STATUS_SUM_MISMATCH,
    Payment,
    PaymentPrice,
)
from app.models.user import User
from app.services.contacts import normalize_tg_username
from app.services.tz import MSK_TZ, _as_utc

logger = logging.getLogger(__name__)

# Во сколько по Москве после последнего дня окна закрывается доступ.
# Заказчик: «не оплатил к утру после окна — доступ закрыт». Точный час —
# открытый вопрос № 2 плана (00:00 или 09:00); до ответа — 09:00.
CUTOFF_HOUR_MSK = 9

# Крайний день окна — не позже 28-го, чтобы окно было в любом месяце.
WINDOW_LAST_DAY_MAX = 28

ORDER_PREFIX = "ap-"

MONTHS_NOM = (
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


class PaymentUnavailable(Exception):
    """Оплату сейчас создать нельзя — текст годится для показа человеку."""


# ---------------------------------------------------------------------------
# Окно и месяц
# ---------------------------------------------------------------------------

def month_start(value: date) -> date:
    return value.replace(day=1)


def add_months(period: date, months: int) -> date:
    index = period.year * 12 + (period.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def period_label(period: date) -> str:
    """«октябрь 2026»."""
    return f"{MONTHS_NOM[period.month]} {period.year}"


def has_window(user: User) -> bool:
    """Ученик в автоматической оплате — окно задано."""
    return bool(user.pay_window_start and user.pay_window_end)


def validate_window(start: int | None, end: int | None) -> str | None:
    """Ошибка окна для показа человеку, `None` — окно годится."""
    if start is None and end is None:
        return None
    if start is None or end is None:
        return "Укажите оба дня окна оплаты"
    if not 1 <= start <= end <= WINDOW_LAST_DAY_MAX:
        return f"Окно оплаты — дни месяца с 1 по {WINDOW_LAST_DAY_MAX}, «с» не позже «по»"
    return None


def window_opens_at(user: User, period: date) -> datetime:
    """Начало окна месяца `period` — 00:00 первого дня окна по Москве, в UTC."""
    opens = datetime.combine(period.replace(day=user.pay_window_start), time(0), MSK_TZ)
    return opens.astimezone(timezone.utc)


def window_cutoff(user: User, period: date) -> datetime:
    """Отсечка окна месяца `period` — утро после последнего дня окна, в UTC."""
    last_day = period.replace(day=user.pay_window_end)
    cutoff = datetime.combine(last_day + timedelta(days=1), time(CUTOFF_HOUR_MSK), MSK_TZ)
    return cutoff.astimezone(timezone.utc)


def paid_until_after(user: User, period: date) -> datetime:
    """Каким станет `paid_until`, когда оплачен месяц `period`."""
    return window_cutoff(user, add_months(period, 1))


def due_period(user: User, now: datetime) -> date:
    """Какой месяц ученик должен оплатить следующим.

    `paid_until` — отсечка окна следующего неоплаченного месяца, значит этот
    месяц — тот, в котором лежит отсечка (минус час: отсечка может прийтись
    на 1-е число, если окно кончается последним днём месяца). Оплат ещё не
    было — текущий месяц по Москве.
    """
    if user.paid_until is not None:
        cutoff_msk = _as_utc(user.paid_until).astimezone(MSK_TZ) - timedelta(hours=CUTOFF_HOUR_MSK + 1)
        return month_start(cutoff_msk.date())
    return month_start(_as_utc(now).astimezone(MSK_TZ).date())


def can_pay_now(user: User, now: datetime) -> bool:
    """Показывать ли кнопку «Оплатить»: окно должного месяца открылось.

    До окна кнопки нет (решение владельца 30.09.2026). После отсечки кнопка
    остаётся — иначе должнику нечем открыть доступ.
    """
    if not has_window(user):
        return False
    return _as_utc(now) >= window_opens_at(user, due_period(user, now))


# ---------------------------------------------------------------------------
# Цена
# ---------------------------------------------------------------------------

def price_kop(db: DBSession, user: User) -> int | None:
    """Цена месяца для ученика сейчас: индивидуальная или из справочника.

    `None` — цену взять неоткуда (нет тарифа, набора или строки справочника);
    ссылку тогда не создаём, а не выставляем ноль.
    """
    if user.pay_price_kop:
        return user.pay_price_kop
    if not user.tariff or not user.pay_cohort:
        return None
    row = (
        db.query(PaymentPrice)
        .filter(PaymentPrice.tariff == user.tariff, PaymentPrice.cohort == user.pay_cohort)
        .first()
    )
    return row.amount_kop if row else None


def rub_text(amount_kop: int) -> str:
    """13 255 ₽ / 13 255,50 ₽ — для людей."""
    rub, kop = divmod(amount_kop, 100)
    text = f"{rub:,}".replace(",", " ")
    return f"{text},{kop:02d} ₽" if kop else f"{text} ₽"


def kop_to_rub_str(amount_kop: int) -> str:
    """13255.00 — для Продамуса."""
    rub, kop = divmod(amount_kop, 100)
    return f"{rub}.{kop:02d}"


def rub_str_to_kop(raw: str | None) -> int | None:
    """«13255.00» / «13255» / «13255,5» → копейки; мусор — `None`."""
    text = (raw or "").strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return int((value * 100).to_integral_value())


# ---------------------------------------------------------------------------
# Подпись Продамуса
# ---------------------------------------------------------------------------
#
# Повторяет `Hmac::create` из PHP-библиотеки Продамуса (документация сверена
# через Context7 07.10.2026, `/websites/help_prodamus_ru_payform`):
# 1) все значения — строки; 2) ключи отсортированы, вложенные тоже;
# 3) `json_encode(..., JSON_UNESCAPED_UNICODE)` — без пробелов, кириллица как
# есть, `/` экранирован как `\/` (PHP делает это по умолчанию, Python нет);
# 4) HMAC-SHA256 секретным ключом, hex. Сверка — без учёта регистра.

def _stringify(value):
    if isinstance(value, dict):
        return {str(k): _stringify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify(v) for v in value]
    if value is None:
        return ""
    return str(value)


def _php_json(value) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # PHP без JSON_UNESCAPED_LINE_TERMINATORS экранирует и эти два символа.
    return (
        text.replace("/", "\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def prodamus_sign(data: dict, key: str) -> str:
    payload = _php_json(_stringify(data)).encode("utf-8")
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def prodamus_verify(data: dict, key: str, signature: str | None) -> bool:
    if not key or not signature:
        return False
    return hmac.compare_digest(prodamus_sign(data, key), signature.strip().lower())


def nest_form(items) -> dict:
    """Плоские поля формы → вложенная структура, как `$_POST` в PHP.

    `products[0][name]=…` становится `{"products": [{"name": …}]}`: подпись
    Продамус считает по разобранному `$_POST`, и порядок вложенных ключей
    важен. Словарь, у которого все ключи — числа подряд с нуля, становится
    списком: так его кодирует `json_encode`.
    """
    root: dict = {}
    for raw_key, value in items:
        head, _, rest = raw_key.partition("[")
        parts = [head] + ([p.rstrip("]") for p in ("[" + rest).split("[")[1:]] if rest else [])
        node = root
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            if part == "":
                part = str(len(node))
            if last:
                node[part] = value
            else:
                node = node.setdefault(part, {})
                if not isinstance(node, dict):
                    break
    return _lists_from_numeric(root)


def _lists_from_numeric(node):
    if not isinstance(node, dict):
        return node
    converted = {k: _lists_from_numeric(v) for k, v in node.items()}
    keys = list(converted)
    if keys and all(k.isdigit() for k in keys) and sorted(int(k) for k in keys) == list(range(len(keys))):
        return [converted[str(i)] for i in range(len(keys))]
    return converted


def _flatten_query(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Обратное к `nest_form`: вложенное → поля ссылки `products[0][name]`."""
    pairs: list[tuple[str, str]] = []
    items = data.items() if isinstance(data, dict) else enumerate(data)
    for key, value in items:
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, (dict, list)):
            pairs.extend(_flatten_query(value, name))
        else:
            pairs.append((name, value))
    return pairs


# ---------------------------------------------------------------------------
# Ссылка на оплату
# ---------------------------------------------------------------------------

def payments_configured() -> bool:
    return bool(settings.prodamus_form_url and settings.prodamus_secret_key)


def order_number(payment: Payment) -> str:
    return f"{ORDER_PREFIX}{payment.id}"


def payment_id_from_order(order_num: str | None) -> int | None:
    text = (order_num or "").strip()
    if not text.startswith(ORDER_PREFIX):
        return None
    tail = text[len(ORDER_PREFIX):]
    return int(tail) if tail.isdigit() else None


def build_link(payment: Payment, user: User, base_url: str) -> str:
    """Подписанная ссылка на платёжную страницу Продамуса.

    Ссылку собираем и подписываем сами (`GET <страница>?…&signature=…`), без
    запроса `do=link` за короткой: при нажатии «Оплатить» нет похода к
    Продамусу и нечему падать. Предмет расчёта и НДС для чека — из настроек
    магазина в Продамусе, в ссылке их нет.
    """
    tariff = TARIFF_DISPLAY.get(payment.tariff, payment.tariff) if payment.tariff else ""
    name = f"Обучение Apparchi, {period_label(payment.period)}"
    if tariff:
        name = f"Обучение Apparchi, тариф «{tariff}», {period_label(payment.period)}"
    data = {
        "order_id": order_number(payment),
        "products": [{
            "name": name,
            "price": kop_to_rub_str(payment.amount_kop),
            "quantity": "1",
        }],
        "customer_extra": f"Ученик #{user.id}, {user.name or ''}".strip(", "),
        "urlSuccess": f"{base_url}/cabinet/personal?paid=1",
        "urlReturn": f"{base_url}/cabinet/personal",
        "sys": "",
    }
    if user.phone:
        data["customer_phone"] = user.phone
    if user.email:
        data["customer_email"] = user.email
    if payment.link_expires_at is not None:
        data["link_expired"] = (
            _as_utc(payment.link_expires_at).astimezone(MSK_TZ).strftime("%Y-%m-%d %H:%M")
        )
    signature = prodamus_sign(data, settings.prodamus_secret_key)
    query = urlencode(_flatten_query(_stringify(data)) + [("signature", signature)])
    form_url = settings.prodamus_form_url.rstrip("/") + "/"
    return f"{form_url}?{query}"


def get_or_create_payment(db: DBSession, user: User, now: datetime, base_url: str) -> Payment:
    """Строка платежа за должный месяц со свежей ссылкой — для кнопки «Оплатить».

    Пока цена та же и ссылка не истекла — отдаём прежнюю: двойное нажатие не
    плодит заказов. Цена изменилась (смена тарифа) — прежняя ссылка
    отменяется, создаётся новая. Отменённую ссылку Продамус всё равно примет
    до её срока; такая оплата засчитается с пометкой (`process_webhook`).
    Не коммитит.
    """
    if not payments_configured():
        raise PaymentUnavailable("Оплата через сайт ещё не подключена")
    if not has_window(user):
        raise PaymentUnavailable("Для вас ещё не настроено окно оплаты")
    if not can_pay_now(user, now):
        raise PaymentUnavailable("Окно оплаты ещё не открылось")
    amount = price_kop(db, user)
    if not amount:
        raise PaymentUnavailable("Сумма оплаты для вас не задана")

    period = due_period(user, now)
    already_paid = (
        db.query(Payment.id)
        .filter(Payment.user_id == user.id, Payment.period == period,
                Payment.kind == KIND_MONTH, Payment.status == STATUS_PAID)
        .first()
    )
    if already_paid:
        raise PaymentUnavailable(f"Оплата за {MONTHS_NOM[period.month]} уже получена")

    pending = (
        db.query(Payment)
        .filter(Payment.user_id == user.id, Payment.period == period,
                Payment.kind == KIND_MONTH, Payment.status == STATUS_PENDING)
        .order_by(Payment.id.desc())
        .all()
    )
    for row in pending:
        alive = row.link_expires_at is None or _as_utc(row.link_expires_at) > _as_utc(now)
        if row.amount_kop == amount and alive and row.link_url:
            return row
        row.status = STATUS_CANCELLED
        row.note = _append_note(row.note, "ссылка заменена новой")

    # Ссылка живёт до отсечки окна; должнику после отсечки — сутки.
    expires = window_cutoff(user, period)
    if expires <= _as_utc(now):
        expires = _as_utc(now) + timedelta(days=1)
    payment = Payment(
        user_id=user.id,
        period=period,
        kind=KIND_MONTH,
        amount_kop=amount,
        tariff=user.tariff or "",
        status=STATUS_PENDING,
        source=SOURCE_PRODAMUS,
        link_expires_at=expires,
    )
    db.add(payment)
    db.flush()
    payment.link_url = build_link(payment, user, base_url)
    return payment


def student_payment_view(db: DBSession, user: User, now: datetime) -> dict | None:
    """Что показать ученику на «Личной информации» про оплату.

    `None` — блока нет: оплата не подключена, ученик вне автоматической
    оплаты или окно должного месяца ещё не открылось.
    """
    if not payments_configured() or not can_pay_now(user, now):
        return None
    period = due_period(user, now)
    amount = price_kop(db, user)
    cutoff = window_cutoff(user, period)
    last_day = period.replace(day=user.pay_window_end)
    return {
        "month": MONTHS_NOM[period.month],
        "amount_text": rub_text(amount) if amount else "",
        "has_price": bool(amount),
        "until_text": f"{last_day.day:02d}.{last_day.month:02d}",
        "overdue": _as_utc(now) >= cutoff,
    }


# ---------------------------------------------------------------------------
# Зачисление — одна точка для вебхука и ручной отметки
# ---------------------------------------------------------------------------

def _append_note(note: str | None, text: str) -> str:
    return f"{note}; {text}" if note else text


def _who(user: User) -> str:
    tg = normalize_tg_username(user.tg_username or "")
    return f"{user.name} (@{tg})" if tg else (user.name or f"#{user.id}")


def _notify_staff(db: DBSession, title: str, text: str) -> list[Notification]:
    rows = []
    for recipient_id in sorted(PAYMENT_NOTIFY_RECIPIENT_IDS):
        if db.get(User, recipient_id) is None:
            continue
        notif = Notification(user_id=recipient_id, title=title, text=text)
        db.add(notif)
        rows.append(notif)
    return rows


def record_paid(
    db: DBSession,
    payment: Payment,
    *,
    now: datetime,
    source: str,
    paid_sum_kop: int,
    performed_by_id: int,
    prodamus_order_id: str | None = None,
    note: str | None = None,
) -> list[Notification]:
    """Платёж оплачен: статус, продление доступа, журнал, уведомления.

    Единственное место зачисления: вебхук Продамуса и ручная отметка ГП идут
    сюда, иначе автоматическая и ручная оплата разошлись бы в мелочах.
    Срок только растёт: оплата старого месяца не укорачивает уже оплаченный.
    Другие неоплаченные ссылки на этот месяц отменяются, чтобы ученик не
    заплатил дважды. Не коммитит; уведомления разослать после commit
    (`notify_many`).
    """
    user = db.get(User, payment.user_id)
    payment.status = STATUS_PAID
    payment.source = source
    payment.paid_at = now
    payment.paid_sum_kop = paid_sum_kop
    if prodamus_order_id:
        payment.prodamus_order_id = prodamus_order_id
    if source == SOURCE_MANUAL:
        payment.marked_by_id = performed_by_id
    if note:
        payment.note = _append_note(payment.note, note)

    for other in (
        db.query(Payment)
        .filter(Payment.user_id == payment.user_id, Payment.period == payment.period,
                Payment.kind == payment.kind, Payment.status == STATUS_PENDING,
                Payment.id != payment.id)
        .all()
    ):
        other.status = STATUS_CANCELLED
        other.note = _append_note(other.note, f"месяц оплачен платежом #{payment.id}")

    extended = ""
    if user is not None and has_window(user) and payment.kind == KIND_MONTH:
        new_until = paid_until_after(user, payment.period)
        if user.paid_until is None or _as_utc(user.paid_until) < new_until:
            user.paid_until = new_until
            extended = f", оплачено до {new_until.astimezone(MSK_TZ).strftime('%d.%m.%Y %H:%M')}"

    month = MONTHS_NOM[payment.period.month]
    db.add(AuditLog(
        action="payment_paid",
        performed_by_id=performed_by_id,
        target_user_id=payment.user_id,
        details=(f"Оплата #{payment.id} за {period_label(payment.period)}: "
                 f"{rub_text(paid_sum_kop)}, {'вручную' if source == SOURCE_MANUAL else 'Продамус'}"
                 f"{extended}")[:1000],
    ))

    created: list[Notification] = []
    if user is not None:
        student_note = Notification(
            user_id=user.id,
            title=f"Оплата за {month} принята",
            text=f"Получили {rub_text(paid_sum_kop)}. Спасибо!",
        )
        db.add(student_note)
        created.append(student_note)
        how = "перевод на счёт, отметил ГП" if source == SOURCE_MANUAL else "Продамус"
        created += _notify_staff(
            db,
            f"Оплата: {_who(user)}",
            f"{_who(user)} оплатил(а) {month}, {rub_text(paid_sum_kop)} ({how}).",
        )
    return created


@dataclass
class WebhookResult:
    """Итог разбора вебхука. `status_code` уходит Продамусу: не 200 — повтор."""

    status_code: int
    message: str
    notifications: list[Notification] = field(default_factory=list)


def process_webhook(db: DBSession, data: dict, now: datetime) -> WebhookResult:
    """Разбор уже проверенного по подписи уведомления Продамуса. Не коммитит.

    Любой исход, кроме сбоя базы, — ответ 200: иначе Продамус будет повторять
    одно и то же уведомление, а решать спорный платёж всё равно человеку, и
    ему уходит уведомление.
    """
    status = str(data.get("payment_status") or "").strip().lower()
    order_num = str(data.get("order_num") or "")
    prodamus_order_id = str(data.get("order_id") or "").strip() or None
    paid_sum = rub_str_to_kop(str(data.get("sum") or ""))

    if status != "success":
        logger.info("Prodamus: заказ %s, статус %s — пропускаем", order_num, status)
        return WebhookResult(200, f"status {status or '—'} ignored")

    payment_id = payment_id_from_order(order_num)
    payment = db.get(Payment, payment_id) if payment_id else None
    if payment is None:
        logger.warning("Prodamus: оплата по неизвестному заказу %r (%s), сумма %s",
                       order_num, prodamus_order_id, data.get("sum"))
        notes = _notify_staff(
            db, "Оплата без заказа на платформе",
            f"Продамус прислал оплату {data.get('sum')} ₽ по заказу «{order_num or '—'}» "
            f"(№ {prodamus_order_id or '—'}), на платформе такого платежа нет. "
            f"Проверьте в кабинете Продамуса: {data.get('customer_extra') or ''}".strip(),
        )
        return WebhookResult(200, "unknown order", notes)

    if payment.status == STATUS_PAID:
        # Повтор того же уведомления — ничего не меняем.
        if not prodamus_order_id or payment.prodamus_order_id == prodamus_order_id:
            return WebhookResult(200, "already paid")
        # Тот же месяц оплачен вторым заказом — деньги не терять, решает человек.
        user = db.get(User, payment.user_id)
        notes = _notify_staff(
            db, "Двойная оплата",
            f"{_who(user) if user else payment.user_id}: за "
            f"{period_label(payment.period)} пришла вторая оплата "
            f"{data.get('sum')} ₽ (Продамус № {prodamus_order_id}). Возврат или зачёт в "
            "следующий месяц — решите вручную.",
        )
        return WebhookResult(200, "duplicate payment", notes)

    if paid_sum is None or paid_sum != payment.amount_kop:
        payment.status = STATUS_SUM_MISMATCH
        payment.paid_sum_kop = paid_sum
        payment.paid_at = now
        if prodamus_order_id:
            payment.prodamus_order_id = prodamus_order_id
        user = db.get(User, payment.user_id)
        notes = _notify_staff(
            db, "Оплата не на ту сумму",
            f"{_who(user) if user else payment.user_id}: за "
            f"{period_label(payment.period)} ждали {rub_text(payment.amount_kop)}, пришло "
            f"{data.get('sum')} ₽. Доступ не продлён — проверьте вручную.",
        )
        return WebhookResult(200, "sum mismatch", notes)

    note = None
    if payment.status == STATUS_CANCELLED:
        note = "оплачена отменённая ссылка"
    notes = record_paid(
        db, payment, now=now, source=SOURCE_PRODAMUS, paid_sum_kop=paid_sum,
        performed_by_id=payment.user_id, prodamus_order_id=prodamus_order_id, note=note,
    )
    if note:
        user = db.get(User, payment.user_id)
        notes += _notify_staff(
            db, "Оплата по отменённой ссылке",
            f"{_who(user) if user else payment.user_id}: оплатил(а) "
            f"{period_label(payment.period)} по ссылке, которую уже заменили "
            f"(цена {rub_text(payment.amount_kop)}). Платёж засчитан — проверьте, "
            "не нужна ли доплата.",
        )
    return WebhookResult(200, "paid", notes)
