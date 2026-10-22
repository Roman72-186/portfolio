"""Ежемесячная оплата обучения через Продамус.

План — `plans/2026-09-30-apparchi-monthly-payments.md`, инструкция заказчику —
`docs/инструкция-продамус.md`. Начато 07.10.2026.

Как устроено:

- У ученика своё **окно оплаты** — дни месяца «с» и «по» (`User.pay_window_*`).
  Месяц платят внутри его окна: октябрь — 10–15 октября. Окно 25–30 в феврале
  кончается 28-го (29-го): день за концом месяца — последний день месяца.
- **Отсечка** — 2:00 по Москве после последнего дня окна (`CUTOFF_HOUR_MSK`).
  Не оплатил к отсечке — при включённой блокировке кабинет закрывается
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
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.constants import (
    PAYMENT_NOTIFY_RECIPIENT_IDS, PAYMENTS_EXTRA_USER_IDS, PROGRAM_HOLIDAY, REPORT_EXCLUDED_USER_IDS,
    TARIFF_DISPLAY, TARIFFS_CURRENT,
)
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.payment import (
    COHORT_LABELS,
    COHORTS,
    KIND_HOLIDAY,
    KIND_MONTH,
    SOURCE_MANUAL,
    SOURCE_PRODAMUS,
    STATUS_CANCELLED,
    STATUS_PAID,
    STATUS_PENDING,
    STATUS_REFUNDED,
    STATUS_SUM_MISMATCH,
    Payment,
    PaymentPrice,
)
from app.models.intake_link import IntakeLink
from app.models.tag import UserTag
from app.models.user import User
from app.services import intake_link as intake_link_service
from app.services.contacts import normalize_tg_username
from app.services.tags import add_tag_to_user, remove_tag_from_user
from app.services.tz import MSK_TZ, _as_utc, msk_text
from app.services.user_management import apply_tariff_change

logger = logging.getLogger(__name__)

# Кому из сотрудников открыто всё про оплату: группа «Оплата» в карточке
# ученика, «Оплата списком», экран «Оплаты» с отметкой, отменой, возвратом и
# Excel. Владелец 14.10.2026: «только ГП открыть всё по оплатам, что есть у
# СА» (до этого 08.10 — только СА, 13.10 — ГП только смотрит). По роли, не по
# рангу: у модератора уровень ГП (`rbac.effective_role_rank`), а оплата ему
# не открыта. Плюс поимённо — `constants.PAYMENTS_EXTRA_USER_IDS` (Александрия,
# 14.10.2026). Одно место — его читают шаблоны, карточка и все роуты.
PAYMENTS_ROLE_NAMES = frozenset({"админ", "суперадмин"})


def is_payments_staff(user: dict | None) -> bool:
    """Открыто ли сотруднику всё про оплату: ГП и суперадмин по роли, ещё —
    поимённо (`PAYMENTS_EXTRA_USER_IDS`)."""
    if not user:
        return False
    return user.get("role_name") in PAYMENTS_ROLE_NAMES or user.get("user_id") in PAYMENTS_EXTRA_USER_IDS


# Адреса оплаты: экран «Оплаты» (с Excel, отметкой, отменой, возвратом),
# «Оплата списком» и сохранение группы «Оплата» в карточке. На них решает
# `is_payments_staff`, а не белый список модератора и не действие
# `people:students` раздела «Люди» (`dependencies.get_current_user`): иначе
# поимённый доступ (Александрия, модератор) упирался бы в них.
_PAYMENTS_PATH = re.compile(
    r"^/cabinet/superadmin/(payments|payment-import)(/.*)?$"
    r"|^/cabinet/superadmin/users/\d+/payment$"
)


def is_payments_path(path: str) -> bool:
    return bool(_PAYMENTS_PATH.match(path))


def is_payments_viewer(user: dict | None) -> bool:
    """Видит ли сотрудник статистику оплат. С 14.10.2026 — те же, кто меняет
    (`is_payments_staff`); отдельная функция осталась для шаблонов."""
    return is_payments_staff(user)


# Во сколько по Москве после последнего дня окна закрывается доступ.
# Служба заботы 10.10.2026: «в 2:00 ночи» — окно 10–15 закрывается 16-го в 2:00.
CUTOFF_HOUR_MSK = 2

# Окно ставят теми днями, что говорит школа (10–15, 25–30), вплоть до 31-го.
# В месяце короче окно кончается его последним днём (`window_day`): школа
# 11.10.2026 — «февраль до конца месяца».
WINDOW_LAST_DAY_MAX = 31

ORDER_PREFIX = "ap-"
# Канал школы в тексте после оплаты (`paid_content`), дословно по школе 12.10.2026.
SCHOOL_CHANNEL_URL = "https://t.me/creativeanalytic"

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


def window_day(period: date, day: int) -> date:
    """День окна в месяце `period`; за концом месяца — его последний день.

    Окно 25–30 в феврале — 25–28, окно 30–31 в апреле — 30–30. Окно хранит
    дни как их назвала школа, укорачивается оно только здесь — так что
    открытие, отсечка и тексты всегда говорят об одном и том же дне.
    """
    last = calendar.monthrange(period.year, period.month)[1]
    return period.replace(day=min(day, last))


def window_last_day(user: User, period: date) -> date:
    """Последний день окна месяца `period` — «оплатить до … включительно»."""
    return window_day(period, user.pay_window_end)


def window_opens_at(user: User, period: date) -> datetime:
    """Начало окна месяца `period` — 00:00 первого дня окна по Москве, в UTC."""
    opens = datetime.combine(window_day(period, user.pay_window_start), time(0), MSK_TZ)
    return opens.astimezone(timezone.utc)


def window_cutoff(user: User, period: date) -> datetime:
    """Отсечка окна месяца `period` — 2:00 после последнего дня окна, в UTC."""
    last_day = window_last_day(user, period)
    cutoff = datetime.combine(last_day + timedelta(days=1), time(CUTOFF_HOUR_MSK), MSK_TZ)
    return cutoff.astimezone(timezone.utc)


def paid_until_after(user: User, period: date) -> datetime:
    """Каким станет `paid_until`, когда оплачен месяц `period`."""
    return window_cutoff(user, add_months(period, 1))


def due_period(user: User, now: datetime) -> date:
    """Какой месяц ученик должен оплатить следующим.

    `paid_until` — отсечка окна следующего неоплаченного месяца, значит этот
    месяц — тот, в котором лежит последний день окна, за сутки до отсечки
    (сама отсечка приходится на 1-е число, если окно кончается последним днём
    месяца). Сутки, а не час: срок, поставленный при другом `CUTOFF_HOUR_MSK`
    (до 11.10.2026 — 9:00), должен указывать на тот же месяц. Оплат ещё не
    было — текущий месяц по Москве.
    """
    if user.paid_until is not None:
        last_day_msk = _as_utc(user.paid_until).astimezone(MSK_TZ) - timedelta(days=1)
        return month_start(last_day_msk.date())
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
    return reference_price_kop(db, user.tariff, user.pay_cohort)


def reference_price_kop(db: DBSession, tariff: str | None, cohort: str | None) -> int | None:
    """Цена из справочника «тариф × набор», без индивидуальной."""
    if not tariff or not cohort:
        return None
    row = (
        db.query(PaymentPrice)
        .filter(PaymentPrice.tariff == tariff, PaymentPrice.cohort == cohort)
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


def _tariff_name(tariff: str | None) -> str:
    """Тариф так, как его видит ученик: «Я с вами», а не код из базы."""
    return TARIFF_DISPLAY.get(tariff, tariff) if tariff else ""


def tariff_to_pay(user: User) -> str:
    """Тариф, за который ученик платит ближайшую оплату: следующий, если его
    поставили (`User.pay_next_tariff`), иначе текущий. Он и в названии чека,
    и в строке платежа — по нему `record_paid` переключает тариф."""
    return user.pay_next_tariff or user.tariff or ""


def paid_thanks_text(tariff: str | None, *, with_contact: bool = True) -> str:
    """Благодарность после оплаты: страница Продамуса (`paid_content`),
    уведомление ученику «Оплата принята» и плашка на сайте после возврата.
    Дословно по владельцу 12.10.2026. На плашке сайта строки про
    администратора канала нет (`with_contact=False`, владелец 13.10.2026)."""
    name = _tariff_name(tariff)
    on_tariff = f" на тарифе {name}" if name else ""
    text = f"Благодарю за оплату следующего месяца годового онлайн-курса{on_tariff}"
    if with_contact:
        text += f"\nАдминистратор тг-канала {SCHOOL_CHANNEL_URL} свяжется с вами"
    return text


def _track_receipt_title(track: IntakeLink) -> str:
    """Трек в чеке. «Попробуй обучение» на платформе — в чеке «Попробуй
    подготовку» (школа 19.10.2026); остальные — как названы на платформе."""
    title = track.title.strip()
    if intake_link_service.track_color(title) == "violet":
        return "Попробуй подготовку"
    return title


def _track_is_materials(track: IntakeLink) -> bool:
    """1 трек — доступ к материалам, 2 трек и «Попробуй подготовку» — онлайн-курс
    (школа 19.10.2026). Номер трека читается из названия, отдельного поля нет."""
    return re.search(r"(?<!\d)1\s*трек", track.title.casefold()) is not None


def holiday_receipt(track: IntakeLink | None) -> tuple[str, str]:
    """Название позиции в чеке и текст после оплаты (`paid_content`) для трека,
    дословно по школе 19.10.2026."""
    if track is None:
        return "Каникулярный набор", holiday_thanks_text(None)
    title = _track_receipt_title(track)
    if _track_is_materials(track):
        name, thanks = f"Доступ к материалам {title}", f"Благодарю за оплату доступа к материалам {title}"
    else:
        name, thanks = f"Онлайн-курс {title}", f"Благодарю за оплату курса {title}"
    return name, f"{thanks}\nАдминистратор тг-канала {SCHOOL_CHANNEL_URL} свяжется с тобой"


def holiday_thanks_text(track: IntakeLink | None) -> str:
    """Благодарность за оплату трека: уведомление ученику и плашка на странице
    треков. На странице Продамуса — текст школы из `holiday_receipt`."""
    title = f"«{track.title}» " if track is not None else ""
    return (f"Спасибо за оплату! Трек {title}открыт: вернись на платформу и заполни "
            "короткую анкету.")


def payment_subject(db: DBSession, payment: Payment) -> str:
    """За что платёж — для журнала и уведомлений: месяц или трек набора."""
    if payment.kind == KIND_HOLIDAY:
        track = db.get(IntakeLink, payment.intake_link_id) if payment.intake_link_id else None
        return f"трек «{track.title}»" if track is not None else "трек каникулярного набора"
    return period_label(payment.period)


def build_link(payment: Payment, user: User, base_url: str, *, track: IntakeLink | None = None) -> str:
    """Подписанная ссылка на платёжную страницу Продамуса.

    Ссылку собираем и подписываем сами (`GET <страница>?…&signature=…`), без
    запроса `do=link` за короткой: при нажатии «Оплатить» нет похода к
    Продамусу и нечему падать. Предмет расчёта и НДС для чека — из настроек
    магазина в Продамусе, в ссылке их нет.

    `do=pay` обязателен и входит в подпись: без него Продамус не узнаёт ссылку
    и показывает пустую заблокированную форму «Согласуйте заказ с менеджером»
    (12.10.2026, первая живая оплата).

    Название, категория (`type=course` — «Доступ к курсу») и текст после оплаты
    (`paid_content`) — дословно по школе, 12.10.2026.
    """
    if payment.kind == KIND_HOLIDAY:
        # Трек каникулярного набора (17.10.2026): в чеке — название трека,
        # возврат — на страницу треков, она же ждёт подтверждения оплаты.
        name, thanks = holiday_receipt(track)
        back = f"{base_url}/cabinet/personal/track"
    else:
        tariff = _tariff_name(payment.tariff)
        name = f"Годовой онлайн-курс тариф {tariff}" if tariff else "Годовой онлайн-курс"
        thanks = paid_thanks_text(payment.tariff)
        back = f"{base_url}/cabinet/personal"
    data = {
        "order_id": order_number(payment),
        "products": [{
            "name": name,
            "price": kop_to_rub_str(payment.amount_kop),
            "quantity": "1",
            "type": "course",
        }],
        "paid_content": thanks,
        "customer_extra": f"Ученик #{user.id}, {user.name or ''}".strip(", "),
        "urlSuccess": f"{back}?paid=1",
        "urlReturn": back,
        "sys": "",
        "do": "pay",
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
        raise PaymentUnavailable("Оплата за следующий месяц обучения уже получена")

    pending = (
        db.query(Payment)
        .filter(Payment.user_id == user.id, Payment.period == period,
                Payment.kind == KIND_MONTH, Payment.status == STATUS_PENDING)
        .order_by(Payment.id.desc())
        .all()
    )
    for row in pending:
        alive = row.link_expires_at is None or _as_utc(row.link_expires_at) > _as_utc(now)
        # Тариф — в названии чека: поставили следующий тариф — нужна новая ссылка.
        # Адрес страницы — тоже: школа сменила его 13.10.2026, старый отвечает
        # 404, и живая ссылка на него вела бы ученика в никуда.
        same_page = (row.link_url or "").startswith(settings.prodamus_form_url)
        if (row.amount_kop == amount and alive and row.link_url and same_page
                and row.tariff == tariff_to_pay(user)):
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
        tariff=tariff_to_pay(user),
        status=STATUS_PENDING,
        source=SOURCE_PRODAMUS,
        link_expires_at=expires,
    )
    db.add(payment)
    db.flush()
    payment.link_url = build_link(payment, user, base_url)
    return payment


def open_track(db: DBSession, user: User, track: IntakeLink) -> bool:
    """Открыть ученику трек: тег трека (по нему открывается программа) и срок
    доступа из трека, только вперёд. Зовут оплата (`record_paid`) и тестовый
    проход служебного аккаунта (`grant_track_for_test`). Не коммитит."""
    if track.tag_id is None:
        return False
    add_tag_to_user(db, user.id, track.tag_id)
    if track.access_until is not None and (
        user.access_until is None or _as_utc(user.access_until) < _as_utc(track.access_until)
    ):
        user.access_until = track.access_until
    return True


def is_test_account(user_id: int) -> bool:
    """Служебный аккаунт (`REPORT_EXCLUDED_USER_IDS`) — им владелец и школа
    проверяют путь ученика (владелец 19.10.2026: «если я нажимаю, то нужно
    просто пропустить»)."""
    return user_id in REPORT_EXCLUDED_USER_IDS


def grant_track_for_test(db: DBSession, user: User, track: IntakeLink, *, now: datetime) -> None:
    """Тестовый проход: служебный аккаунт получает трек без оплаты — без
    Продамуса и без строки платежа (иначе тест попал бы в «Оплаты» и итоги).
    След — журнал `holiday_track_test_grant`. Не коммитит."""
    if not is_test_account(user.id):
        raise PaymentUnavailable("Без оплаты трек открывается только служебным аккаунтам")
    if track not in intake_link_service.tracks_on_sale(db, now):
        raise PaymentUnavailable("Этот трек сейчас не продаётся")
    if any(owned.id == track.id for owned in intake_link_service.tracks_of(db, user.id)):
        raise PaymentUnavailable("Этот трек уже открыт")
    open_track(db, user, track)
    db.add(AuditLog(
        action="holiday_track_test_grant",
        performed_by_id=user.id,
        target_user_id=user.id,
        details=f"Тестовый проход без оплаты: трек «{track.title}», доступ до {msk_text(user.access_until)}"[:1000],
    ))


def get_or_create_track_payment(
    db: DBSession, user: User, track: IntakeLink, now: datetime, base_url: str,
) -> Payment:
    """Строка платежа за трек каникулярного набора со свежей ссылкой — кнопка
    «Оплатить» на странице треков (17.10.2026). Те же правила, что у месяца:
    живая ссылка с той же ценой отдаётся снова, сменилась цена — прежняя
    отменяется. Ссылка живёт сутки. Не коммитит."""
    if not payments_configured():
        raise PaymentUnavailable("Оплата через сайт ещё не подключена")
    if user.program != PROGRAM_HOLIDAY:
        raise PaymentUnavailable("Треки каникулярного набора оплачивают только его ученики")
    if track not in intake_link_service.tracks_on_sale(db, now):
        raise PaymentUnavailable("Этот трек сейчас не продаётся")
    # Другой трек купить можно (школа 18.10.2026), тот же второй раз — нет.
    if any(owned.id == track.id for owned in intake_link_service.tracks_of(db, user.id)):
        raise PaymentUnavailable("Этот трек уже оплачен")

    pending = (
        db.query(Payment)
        .filter(Payment.user_id == user.id, Payment.kind == KIND_HOLIDAY,
                Payment.intake_link_id == track.id, Payment.status == STATUS_PENDING)
        .order_by(Payment.id.desc())
        .all()
    )
    for row in pending:
        alive = row.link_expires_at is None or _as_utc(row.link_expires_at) > _as_utc(now)
        same_page = (row.link_url or "").startswith(settings.prodamus_form_url)
        if row.amount_kop == track.price_kop and alive and row.link_url and same_page:
            return row
        row.status = STATUS_CANCELLED
        row.note = _append_note(row.note, "ссылка заменена новой")

    payment = Payment(
        user_id=user.id,
        period=month_start(now.astimezone(MSK_TZ).date()),
        kind=KIND_HOLIDAY,
        intake_link_id=track.id,
        amount_kop=track.price_kop,
        tariff="",
        status=STATUS_PENDING,
        source=SOURCE_PRODAMUS,
        link_expires_at=_as_utc(now) + timedelta(days=1),
    )
    db.add(payment)
    db.flush()
    payment.link_url = build_link(payment, user, base_url, track=track)
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
    last_day = window_last_day(user, period)
    return {
        "month": MONTHS_NOM[period.month],
        "amount_text": rub_text(amount) if amount else "",
        "has_price": bool(amount),
        "until_text": f"{last_day.day:02d}.{last_day.month:02d}",
        "overdue": _as_utc(now) >= cutoff,
    }


# ---------------------------------------------------------------------------
# Настройки оплаты ученика — карточка «Учеников» и загрузка списка
# ---------------------------------------------------------------------------

class PaymentSettingsError(ValueError):
    """Настройки оплаты не годятся — текст для показа человеку."""


def paid_month(user: User) -> date | None:
    """Последний оплаченный месяц — обратное к `paid_until_after`.

    `None` — оплат не было или ученик вне автоматической оплаты.
    """
    if user.paid_until is None or not has_window(user):
        return None
    return add_months(due_period(user, user.paid_until), -1)


def parse_paid_month(raw: str | None) -> date | None:
    """«2026-10» из `<input type="month">` → 1 октября 2026; пусто — `None`."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        year, month = (int(part) for part in text.split("-"))
        return date(year, month, 1)
    except ValueError:
        raise PaymentSettingsError("Неверный месяц оплаты") from None


def _window_text(start: int | None, end: int | None) -> str:
    return f"{start}–{end}" if start and end else "—"


def _price_text(amount_kop: int | None) -> str:
    return rub_text(amount_kop) if amount_kop else "по справочнику"


def _month_text(period: date | None) -> str:
    return period_label(period) if period else "—"


def apply_payment_settings(
    db: DBSession,
    performed_by_id: int,
    student: User,
    *,
    window_start: int | None,
    window_end: int | None,
    cohort: str,
    price_kop: int | None,
    paid_through: date | None,
    next_tariff: str | None = None,
) -> bool:
    """Окно оплаты, набор, индивидуальная цена и «оплачено по» ученика.

    Одна точка ручной настройки: блок «Управление» в карточке «Учеников» и
    загрузка списка от заказчика. Это правка, а не оплата: платежа не
    создаёт, ученику и службе заботы ничего не шлёт — деньги зачисляет только
    `record_paid`.

    `paid_through` — последний оплаченный месяц. `paid_until` из него
    считается тем же правилом, что при оплате (`paid_until_after`), поэтому
    смена окна сдвигает срок сама. Пусто — оплат не было: срок не ведётся, и
    по неоплате кабинет не закроется. Срок здесь может и уменьшиться — это
    исправление ошибки, в отличие от оплаты.

    `next_tariff` — тариф с ближайшей оплаты (`User.pay_next_tariff`); `None`
    — не трогать (загрузка списка его не знает), пусто или текущий тариф —
    снять.

    Возвращает True, если что-то изменилось. Не коммитит.
    """
    error = validate_window(window_start, window_end)
    if error:
        raise PaymentSettingsError(error)
    if cohort and cohort not in COHORTS:
        raise PaymentSettingsError("Неверный набор")
    if price_kop is not None and price_kop <= 0:
        raise PaymentSettingsError("Цена должна быть больше нуля")
    if paid_through is not None and not (window_start and window_end):
        raise PaymentSettingsError("Чтобы указать оплаченный месяц, задайте окно оплаты")
    if next_tariff is not None:
        next_tariff = next_tariff.strip()
        if next_tariff and next_tariff not in TARIFFS_CURRENT:
            raise PaymentSettingsError("Неверный тариф после оплаты")
        if next_tariff == (student.tariff or ""):
            next_tariff = ""

    old_window = (student.pay_window_start, student.pay_window_end)
    old_cohort = student.pay_cohort or ""
    old_price = student.pay_price_kop
    old_month = paid_month(student)
    old_until = student.paid_until
    old_next = student.pay_next_tariff or ""

    student.pay_window_start = window_start
    student.pay_window_end = window_end
    student.pay_cohort = cohort
    student.pay_price_kop = price_kop
    student.paid_until = paid_until_after(student, paid_through) if paid_through else None
    if next_tariff is not None:
        student.pay_next_tariff = next_tariff

    changes = []
    if old_window != (window_start, window_end):
        changes.append(f"окно: {_window_text(*old_window)} → {_window_text(window_start, window_end)}")
    if old_cohort != cohort:
        changes.append(f"набор: {COHORT_LABELS.get(old_cohort, '—')} → {COHORT_LABELS.get(cohort, '—')}")
    if old_price != price_kop:
        changes.append(f"цена: {_price_text(old_price)} → {_price_text(price_kop)}")
    if old_month != paid_through or (old_until is None) != (student.paid_until is None):
        changes.append(f"оплачено по: {_month_text(old_month)} → {_month_text(paid_through)}")
    if next_tariff is not None and old_next != next_tariff:
        changes.append(f"тариф после оплаты: {_tariff_name(old_next) or '—'} → {_tariff_name(next_tariff) or '—'}")
    if not changes:
        return False
    db.add(AuditLog(
        action="payment_settings_change",
        performed_by_id=performed_by_id,
        target_user_id=student.id,
        details=("Оплата: " + "; ".join(changes))[:1000],
    ))
    return True


def _rub_input(amount_kop: int | None) -> str:
    """Цена для поля ввода: «12000» или «12000,50»."""
    if not amount_kop:
        return ""
    rub, kop = divmod(amount_kop, 100)
    return f"{rub},{kop:02d}" if kop else str(rub)


def manage_view(db: DBSession, student: User) -> dict:
    """Поля оплаты для блока «Управление» в карточке ученика.

    `mark_*` — что подставить в «Отметить оплату» той же карточки: должный
    месяц и цена ученика. Пишет форма тем же `POST /payments/mark`, что на
    экране «Оплаты», — своей отметки у карточки нет.
    """
    month = paid_month(student)
    reference = reference_price_kop(db, student.tariff, student.pay_cohort)
    now = datetime.now(timezone.utc)
    windowed = has_window(student)
    return {
        "mark_month": due_period(student, now).strftime("%Y-%m") if windowed else "",
        "mark_amount": _rub_input(price_kop(db, student)) if windowed else "",
        "today": _as_utc(now).astimezone(MSK_TZ).date().isoformat(),
        "pay_window_start": student.pay_window_start,
        "pay_window_end": student.pay_window_end,
        "pay_cohort": student.pay_cohort or "",
        "pay_cohorts": [[key, COHORT_LABELS[key]] for key in COHORTS],
        "pay_price": _rub_input(student.pay_price_kop),
        "pay_reference_text": rub_text(reference) if reference else "",
        "paid_month": month.strftime("%Y-%m") if month else "",
        "pay_next_tariff": student.pay_next_tariff or "",
        "pay_tariff_choices": [[t, _tariff_name(t)] for t in TARIFFS_CURRENT],
        "paid_until_text": msk_text(student.paid_until),
        "payments_block_enabled": settings.payments_block_enabled,
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


def track_refund_reason(db: DBSession, payment: Payment, now: datetime) -> str:
    """Оплата трека, которая доступа не даёт, — школе вернуть деньги
    (23.10.2026). Пусто — оплата обычная. Смотреть до зачисления: оно ставит
    тег трека.

    Трек уже был у ученика — вторая оплата того же (две вкладки, ручной тег и
    оплата мимо платформы). Набор по треку закончился — срок трека в прошлом,
    кабинет закрыт сроком, тег ничего не открывает."""
    if payment.kind != KIND_HOLIDAY or not payment.intake_link_id:
        return ""
    track = db.get(IntakeLink, payment.intake_link_id)
    if track is None:
        return ""
    if track.tag_id is not None and db.query(UserTag.user_id).filter(
        UserTag.user_id == payment.user_id, UserTag.tag_id == track.tag_id,
    ).first() is not None:
        return "этот трек у ученика уже был открыт – похоже на вторую оплату"
    if intake_link_service.date_passed(track, now):
        return f"набор по треку закончился {msk_text(track.access_until)} – доступа оплата не даёт"
    return ""


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
    # Ручную отметку ставит сотрудник, который сам знает, за что деньги.
    refund_reason = track_refund_reason(db, payment, now) if source == SOURCE_PRODAMUS else ""
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

    same_thing = (
        db.query(Payment)
        .filter(Payment.user_id == payment.user_id, Payment.period == payment.period,
                Payment.kind == payment.kind, Payment.status == STATUS_PENDING,
                Payment.id != payment.id)
    )
    if payment.kind == KIND_HOLIDAY:
        # Треков за месяц бывает несколько (школа 18.10.2026): отменяем только
        # ссылки на этот же трек, иначе оплата одного трека гасила бы открытую
        # ссылку на другой, и её оплата пришла бы «по отменённой ссылке».
        same_thing = same_thing.filter(Payment.intake_link_id == payment.intake_link_id)
    for other in same_thing.all():
        other.status = STATUS_CANCELLED
        other.note = _append_note(other.note, f"оплачено платежом #{payment.id}")

    extended = ""
    track = None
    if user is not None and payment.kind == KIND_HOLIDAY:
        # Трек каникулярного набора: тег трека открывает его программу, срок —
        # из трека и только вперёд (вход по общей ссылке уже дал свой срок).
        track = db.get(IntakeLink, payment.intake_link_id) if payment.intake_link_id else None
        if track is not None and open_track(db, user, track):
            extended = f", доступ до {msk_text(user.access_until)}"
    if user is not None and has_window(user) and payment.kind == KIND_MONTH:
        new_until = paid_until_after(user, payment.period)
        if user.paid_until is None or _as_utc(user.paid_until) < new_until:
            payment.paid_until_before = user.paid_until
            user.paid_until = new_until
            extended = f", оплачено до {new_until.astimezone(MSK_TZ).strftime('%d.%m.%Y %H:%M')}"

    # Смена тарифа с этой оплаты (владелец 13.10.2026): переключаем только
    # когда оплачен именно новый тариф — не раньше, тариф сразу меняет доступ
    # к материалам. Возврат платежа тариф назад не возвращает — это решает
    # сотрудник в карточке.
    tariff_from = ""
    if user is not None and user.pay_next_tariff and payment.tariff == user.pay_next_tariff:
        tariff_from = user.tariff or ""
        apply_tariff_change(db, performed_by_id, user, user.pay_next_tariff)
        user.pay_next_tariff = ""
        # Прежний тариф — в самом платеже: статистика оплат показывает
        # пометку о смене, журнал хранит её только текстом.
        payment.note = _append_note(
            payment.note, f"тариф сменился с этой оплатой, был {_tariff_name(tariff_from) or '—'}",
        )
        extended += f", тариф {_tariff_name(tariff_from) or '—'} → {_tariff_name(user.tariff)}"

    db.add(AuditLog(
        action="payment_paid",
        performed_by_id=performed_by_id,
        target_user_id=payment.user_id,
        details=(f"Оплата #{payment.id} за {payment_subject(db, payment)}: "
                 f"{rub_text(paid_sum_kop)}, {'вручную' if source == SOURCE_MANUAL else 'Продамус'}"
                 f"{extended}")[:1000],
    ))

    created: list[Notification] = []
    if user is not None:
        student_note = Notification(
            user_id=user.id,
            # Тот же текст, что Продамус показывает после оплаты, — владелец 12.10.2026.
            title="Оплата принята",
            text=(holiday_thanks_text(track) if payment.kind == KIND_HOLIDAY
                  else paid_thanks_text(payment.tariff or user.tariff)),
        )
        db.add(student_note)
        created.append(student_note)
        how = "перевод на счёт, отметил ГП" if source == SOURCE_MANUAL else "Продамус"
        created += _notify_staff(
            db, f"Оплата: {_who(user)}",
            _paid_staff_text(user, payment, paid_sum_kop, how, tariff_from,
                             subject=payment_subject(db, payment)),
        )
        if refund_reason:
            created += _notify_staff(
                db, "Оплата трека: нужен возврат",
                f"{_who(user)}: за {payment_subject(db, payment)} пришло "
                f"{rub_text(paid_sum_kop)}, но {refund_reason}. Верните деньги в "
                "Продамусе и отметьте «Возврат» в «Оплатах» (каникулярный курс).",
            )
    return created


def _paid_period_text(user: User, period: date) -> str:
    """Месяц обучения, за который оплата: «15.10.2026 – 15.11.2026» — от
    последнего дня окна до него же через месяц (школа: «с 15 по 15»)."""
    if not has_window(user):
        return period_label(period)
    start = window_last_day(user, period)
    end = window_last_day(user, add_months(period, 1))
    return f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}"


def _paid_staff_text(user: User, payment: Payment, paid_sum_kop: int, how: str,
                     tariff_from: str = "", subject: str = "") -> str:
    """Уведомление службе заботы об оплате — владелец 13.10.2026: ФИО, почта,
    сумма, период. Отчества на платформе нет — ФИО это `User.name`."""
    tg = normalize_tg_username(user.tg_username or "")
    if payment.kind == KIND_HOLIDAY:
        return "\n".join([
            f"{user.name or f'#{user.id}'} оплатил(а) каникулярный набор: {subject}.",
            f"Telegram: @{tg}" if tg else "Telegram: не указан",
            f"Телефон: {user.phone or 'не указан'}",
            f"Сумма: {rub_text(paid_sum_kop)} ({how})",
        ])
    tariff = _tariff_name(payment.tariff or user.tariff)
    lines = [
        f"{user.name or f'#{user.id}'} оплатил(а) следующий месяц обучения.",
        f"ФИО: {user.name or '—'}",
        f"Почта: {user.email or 'не указана'}",
        f"Telegram: @{tg}" if tg else "Telegram: не указан",
        f"Тариф: {tariff or '—'}"
        + (f" (сменился с этой оплатой, был {_tariff_name(tariff_from) or '—'})" if tariff_from else ""),
        f"Сумма: {rub_text(paid_sum_kop)} ({how})",
        f"Период: {_paid_period_text(user, payment.period)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ручная отметка, её отмена и возврат (экран «Оплаты»)
# ---------------------------------------------------------------------------

def paid_payment_for(db: DBSession, user_id: int, period: date) -> Payment | None:
    """Оплаченный платёж ученика за месяц, если есть."""
    return (
        db.query(Payment)
        .filter(Payment.user_id == user_id, Payment.period == period,
                Payment.kind == KIND_MONTH, Payment.status == STATUS_PAID)
        .first()
    )


def mark_paid_manually(
    db: DBSession,
    student: User,
    *,
    period: date,
    amount_kop: int,
    paid_on: date,
    comment: str,
    performed_by_id: int,
) -> tuple[Payment, list[Notification]]:
    """Оплата мимо Продамуса — перевод на расчётный счёт (п. 3.5 оферты).

    Своей логики зачисления нет: строка платежа и дальше та же `record_paid`,
    что у вебхука. Не коммитит; уведомления разослать после commit.
    """
    if not has_window(student):
        raise PaymentSettingsError("Сначала задайте ученику окно оплаты в карточке")
    if amount_kop <= 0:
        raise PaymentSettingsError("Сумма должна быть больше нуля")
    if paid_payment_for(db, student.id, period):
        raise PaymentSettingsError(f"{period_label(period).capitalize()} уже оплачен")
    payment = Payment(
        user_id=student.id,
        period=period,
        kind=KIND_MONTH,
        amount_kop=amount_kop,
        tariff=tariff_to_pay(student),
        status=STATUS_PENDING,
        source=SOURCE_MANUAL,
    )
    db.add(payment)
    db.flush()
    note = f"перевод на счёт, поступил {paid_on.strftime('%d.%m.%Y')}"
    if comment.strip():
        note += f": {comment.strip()}"
    paid_at = datetime.combine(paid_on, time(12, 0), tzinfo=MSK_TZ).astimezone(timezone.utc)
    notifications = record_paid(
        db, payment, now=paid_at, source=SOURCE_MANUAL, paid_sum_kop=amount_kop,
        performed_by_id=performed_by_id, note=note[:500],
    )
    return payment, notifications


def revert_paid(db: DBSession, payment: Payment, *, refund: bool, performed_by_id: int, now: datetime) -> None:
    """Платёж больше не оплачивает месяц: отмена ручной отметки или возврат.

    Срок ученика откатывается, только если его держал этот платёж: оплата
    более позднего месяца или срок из карточки остаются. Откат — к сроку до
    платежа или к последнему из оставшихся оплаченных месяцев. Отмене
    ошибочной отметки этого хватает: ученик возвращается туда, где был. Возврату
    — нет: если срока до платежа не было, ученик выпал бы из оплаты совсем, и
    кабинет по неоплате не закрылся бы никогда. Поэтому после возврата
    оплаченным остаётся месяц перед возвращённым. Не коммитит.
    """
    if payment.status != STATUS_PAID:
        raise PaymentSettingsError("Платёж не оплачен")
    if not refund and payment.source != SOURCE_MANUAL:
        raise PaymentSettingsError("Отменить можно только ручную отметку. Оплату через Продамус – только возвратом")

    payment.status = STATUS_REFUNDED if refund else STATUS_CANCELLED
    if refund:
        payment.refunded_by_id = performed_by_id
        payment.refunded_at = now

    user = db.get(User, payment.user_id)
    moved = ""
    if user is not None and has_window(user) and payment.kind == KIND_MONTH and user.paid_until is not None:
        contribution = paid_until_after(user, payment.period)
        if _as_utc(user.paid_until) <= contribution:
            remaining = [
                paid_until_after(user, other.period)
                for other in db.query(Payment).filter(
                    Payment.user_id == user.id, Payment.kind == KIND_MONTH,
                    Payment.status == STATUS_PAID, Payment.id != payment.id,
                )
            ]
            candidates = [_as_utc(v) for v in [payment.paid_until_before, *remaining] if v is not None]
            if candidates:
                new_until = max(candidates)
            elif refund:
                new_until = paid_until_after(user, add_months(payment.period, -1))
            else:
                new_until = None
            user.paid_until = new_until
            moved = ", оплачено по: " + (
                new_until.astimezone(MSK_TZ).strftime("%d.%m.%Y %H:%M") if new_until else "не ведётся"
            )

    if user is not None and payment.kind == KIND_HOLIDAY and payment.intake_link_id:
        # Трек без оплаты не держится: снимаем тег, если этот трек ученику не
        # оплатил другой платёж. Срок доступа не трогаем — его дала и общая
        # ссылка, а без тега трека кабинет и так закрыт до страницы оплаты.
        track = db.get(IntakeLink, payment.intake_link_id)
        other_paid = (
            db.query(Payment.id)
            .filter(Payment.user_id == user.id, Payment.kind == KIND_HOLIDAY,
                    Payment.intake_link_id == payment.intake_link_id,
                    Payment.status == STATUS_PAID, Payment.id != payment.id)
            .first()
        )
        if track is not None and track.tag_id is not None and other_paid is None:
            remove_tag_from_user(db, user.id, track.tag_id)
            moved = f", тег трека «{track.title}» снят"

    action = "payment_refunded" if refund else "payment_mark_cancelled"
    what = "Возврат" if refund else "Отмена ручной отметки"
    db.add(AuditLog(
        action=action,
        performed_by_id=performed_by_id,
        target_user_id=payment.user_id,
        details=(f"{what}: оплата #{payment.id} за {payment_subject(db, payment)}, "
                 f"{rub_text(payment.paid_sum_kop or payment.amount_kop)}{moved}")[:1000],
    ))


@dataclass
class WebhookResult:
    """Итог разбора вебхука. `status_code` уходит Продамусу: не 200 — повтор."""

    status_code: int
    message: str
    notifications: list[Notification] = field(default_factory=list)


def webhook_order_paid(db: DBSession, data: dict) -> bool:
    """Платёж из уведомления уже оплачен — после сбоя записи вебхук отличает
    так параллельный повтор от настоящей ошибки (23.10.2026)."""
    payment_id = payment_id_from_order(str(data.get("order_num") or ""))
    payment = db.get(Payment, payment_id) if payment_id else None
    return payment is not None and payment.status == STATUS_PAID


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
        # У трека «следующего месяца» нет — только возврат (23.10.2026).
        decide = ("Нужен возврат – оформите его в Продамусе." if payment.kind == KIND_HOLIDAY
                  else "Возврат или зачёт в следующий месяц — решите вручную.")
        notes = _notify_staff(
            db, "Двойная оплата",
            f"{_who(user) if user else payment.user_id}: за "
            f"{payment_subject(db, payment)} пришла вторая оплата "
            f"{data.get('sum')} ₽ (Продамус № {prodamus_order_id}). {decide}",
        )
        return WebhookResult(200, "duplicate payment", notes)

    # Платёж снят сотрудником — возвратом или отменой ручной отметки. Зачислить
    # его заново значило бы вернуть ученику оплаченный месяц и «Оплата принята»
    # за деньги, которых у школы нет (код-ревью 12.10.2026, P2 № 5: Продамус
    # повторяет уведомление, если не дождался ответа). Стоит до сверки суммы:
    # та переписала бы статус возврата на `sum_mismatch`. Отменённая *ссылка*
    # Продамуса (`cancelled` + `prodamus`) сюда не попадает — её оплата законна.
    if payment.status == STATUS_REFUNDED or (
        payment.status == STATUS_CANCELLED and payment.source == SOURCE_MANUAL
    ):
        user = db.get(User, payment.user_id)
        what = "возвращён" if payment.status == STATUS_REFUNDED else "отменён вручную"
        repeat = bool(prodamus_order_id) and payment.prodamus_order_id == prodamus_order_id
        logger.warning("Prodamus: оплата по снятому платежу #%s (%s), заказ %s",
                       payment.id, payment.status, prodamus_order_id)
        notes = _notify_staff(
            db, "Оплата по снятому платежу",
            f"{_who(user) if user else payment.user_id}: Продамус прислал оплату "
            f"{data.get('sum')} ₽ за {payment_subject(db, payment)} "
            f"(№ {prodamus_order_id or '—'}), а платёж #{payment.id} уже {what}. "
            "Не зачислено. "
            + ("Похоже на повтор прежнего уведомления – проверьте в кабинете Продамуса, "
               "что возврат прошёл." if repeat else
               "Если деньги пришли заново – решите вручную: зачесть или вернуть."),
        )
        return WebhookResult(200, "payment withdrawn", notes)

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
            f"{payment_subject(db, payment)} ждали {rub_text(payment.amount_kop)}, пришло "
            f"{data.get('sum')} ₽. Доступ не продлён — проверьте вручную.",
        )
        return WebhookResult(200, "sum mismatch", notes)

    note = None
    if payment.status == STATUS_CANCELLED:
        note = "оплачена отменённая ссылка"
    # Если зачисление само скажет «нужен возврат», «не нужна ли доплата» про ту
    # же оплату сбивало бы с толку.
    refund_reason = track_refund_reason(db, payment, now)
    notes = record_paid(
        db, payment, now=now, source=SOURCE_PRODAMUS, paid_sum_kop=paid_sum,
        performed_by_id=payment.user_id, prodamus_order_id=prodamus_order_id, note=note,
    )
    if note and not refund_reason:
        user = db.get(User, payment.user_id)
        notes += _notify_staff(
            db, "Оплата по отменённой ссылке",
            f"{_who(user) if user else payment.user_id}: оплатил(а) "
            f"{payment_subject(db, payment)} по ссылке, которую уже заменили "
            f"(цена {rub_text(payment.amount_kop)}). Платёж засчитан — проверьте, "
            "не нужна ли доплата.",
        )
    return WebhookResult(200, "paid", notes)
