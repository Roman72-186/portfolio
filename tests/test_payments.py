"""Ежемесячная оплата через Продамус (`services/payments.py`, `api/payments.py`).

План — `plans/2026-09-30-apparchi-monthly-payments.md`. Проверяется: окно и
отсечка, цена, подпись в формате PHP-библиотеки Продамуса, ссылка, вебхук во
всех исходах и закрытие кабинета по неоплате только при включённом флаге.
"""
import hashlib
import hmac
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

import pytest

from app.config import settings
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.payment import (
    COHORT_BEFORE,
    COHORT_FROM,
    STATUS_CANCELLED,
    STATUS_PAID,
    STATUS_PENDING,
    STATUS_SUM_MISMATCH,
    Payment,
    PaymentPrice,
)
from app.services import payments as ps
from app.services.tz import MSK_TZ

KEY = "test-secret-key"
FORM_URL = "https://apparchi-test.payform.ru/"


def _msk(*args) -> datetime:
    return datetime(*args, tzinfo=MSK_TZ).astimezone(timezone.utc)


@pytest.fixture()
def prodamus(monkeypatch):
    monkeypatch.setattr(settings, "prodamus_form_url", FORM_URL)
    monkeypatch.setattr(settings, "prodamus_secret_key", KEY)


@pytest.fixture()
def no_delivery(monkeypatch):
    """Уведомления создаются в базе, но в Telegram и push не уходят."""
    async def _noop(ids):
        return None
    monkeypatch.setattr("app.api.payments.notify_many", _noop)


@pytest.fixture()
def prices(db):
    db.add_all([
        PaymentPrice(tariff="Я С ВАМИ", cohort=COHORT_BEFORE, amount_kop=1275500),
        PaymentPrice(tariff="Я С ВАМИ", cohort=COHORT_FROM, amount_kop=1325500),
    ])
    db.commit()


@pytest.fixture()
def payer(db, user_factory):
    student = user_factory(tariff="Я С ВАМИ")
    student.pay_window_start = 10
    student.pay_window_end = 15
    student.pay_cohort = COHORT_FROM
    db.commit()
    return student


# ── Окно, отсечка, должный месяц ─────────────────────────────────────────────

def test_window_bounds_in_moscow_time(payer):
    october = date(2026, 10, 1)
    assert ps.window_opens_at(payer, october) == _msk(2026, 10, 10, 0, 0)
    # Отсечка — утро после последнего дня окна.
    assert ps.window_cutoff(payer, october) == _msk(2026, 10, 16, ps.CUTOFF_HOUR_MSK, 0)


def test_paid_month_extends_to_next_month_cutoff(payer):
    assert ps.paid_until_after(payer, date(2026, 10, 1)) == _msk(2026, 11, 16, ps.CUTOFF_HOUR_MSK)
    # Декабрь → январь следующего года.
    assert ps.paid_until_after(payer, date(2026, 12, 1)) == _msk(2027, 1, 16, ps.CUTOFF_HOUR_MSK)


def test_due_period_follows_paid_until(payer):
    now = _msk(2026, 10, 20, 12, 0)
    assert ps.due_period(payer, now) == date(2026, 10, 1)  # оплат не было — текущий месяц
    payer.paid_until = ps.paid_until_after(payer, date(2026, 10, 1))
    assert ps.due_period(payer, now) == date(2026, 11, 1)


def test_due_period_when_window_ends_on_28th(payer):
    """Окно 24–28: отсечка 29-го, месяц тот же."""
    payer.pay_window_start, payer.pay_window_end = 24, 28
    payer.paid_until = ps.paid_until_after(payer, date(2026, 10, 1))
    assert payer.paid_until == _msk(2026, 11, 29, ps.CUTOFF_HOUR_MSK)
    assert ps.due_period(payer, _msk(2026, 11, 1)) == date(2026, 11, 1)


def test_pay_button_only_from_first_day_of_window(payer):
    assert not ps.can_pay_now(payer, _msk(2026, 10, 9, 23, 59))
    assert ps.can_pay_now(payer, _msk(2026, 10, 10, 0, 0))
    # После отсечки кнопка остаётся — должнику нечем иначе открыть доступ.
    assert ps.can_pay_now(payer, _msk(2026, 10, 25))


def test_no_window_no_payments(user_factory):
    student = user_factory()
    assert not ps.has_window(student)
    assert not ps.can_pay_now(student, _msk(2026, 10, 12))


@pytest.mark.parametrize("start,end,ok", [
    (10, 15, True), (1, 28, True), (None, None, True),
    (15, 10, False), (0, 5, False), (20, 31, False), (10, None, False),
])
def test_validate_window(start, end, ok):
    assert (ps.validate_window(start, end) is None) is ok


# ── Цена ─────────────────────────────────────────────────────────────────────

def test_price_from_reference_by_tariff_and_cohort(db, prices, payer):
    assert ps.price_kop(db, payer) == 1325500
    payer.pay_cohort = COHORT_BEFORE
    assert ps.price_kop(db, payer) == 1275500


def test_individual_price_overrides_reference(db, prices, payer):
    payer.pay_price_kop = 1000000
    assert ps.price_kop(db, payer) == 1000000


def test_no_price_when_reference_missing(db, payer):
    assert ps.price_kop(db, payer) is None


@pytest.mark.parametrize("raw,kop", [
    ("13255.00", 1325500), ("13255", 1325500), ("13255,5", 1325550),
    ("", None), ("abc", None), ("-1", None),
])
def test_rub_str_to_kop(raw, kop):
    assert ps.rub_str_to_kop(raw) == kop


def test_rub_text():
    assert ps.rub_text(1325500) == "13 255 ₽"
    assert ps.rub_text(1325550) == "13 255,50 ₽"


# ── Подпись ──────────────────────────────────────────────────────────────────

def test_signature_matches_php_json_encode_by_hand():
    """Эталон собран руками по правилам `json_encode(..., JSON_UNESCAPED_UNICODE)`:
    ключи по алфавиту на всех уровнях, без пробелов, кириллица как есть,
    `/` экранирован, числа — строками."""
    data = {
        "sum": 100,
        "order_num": "ap-1",
        "products": [{"quantity": 1, "name": "Урок 1/2"}],
    }
    expected_json = '{"order_num":"ap-1","products":[{"name":"Урок 1\\/2","quantity":"1"}],"sum":"100"}'
    expected = hmac.new(KEY.encode(), expected_json.encode("utf-8"), hashlib.sha256).hexdigest()
    assert ps.prodamus_sign(data, KEY) == expected


def test_verify_is_case_insensitive_and_rejects_wrong_key():
    data = {"a": "1"}
    sign = ps.prodamus_sign(data, KEY)
    assert ps.prodamus_verify(data, KEY, sign.upper())
    assert not ps.prodamus_verify(data, "other", sign)
    assert not ps.prodamus_verify(data, KEY, None)
    assert not ps.prodamus_verify(data, "", sign)


def test_nest_form_builds_php_post_structure():
    items = [
        ("order_num", "ap-1"),
        ("products[0][name]", "A"),
        ("products[0][price]", "1.00"),
        ("products[1][name]", "B"),
    ]
    assert ps.nest_form(items) == {
        "order_num": "ap-1",
        "products": [{"name": "A", "price": "1.00"}, {"name": "B"}],
    }


# ── Ссылка ───────────────────────────────────────────────────────────────────

def test_payment_link_is_signed_and_reuses_pending(db, prices, payer, prodamus):
    now = _msk(2026, 10, 12, 10, 0)
    payment = ps.get_or_create_payment(db, payer, now, "https://apparchi.ru")
    db.commit()

    assert payment.period == date(2026, 10, 1)
    assert payment.amount_kop == 1325500
    assert payment.status == STATUS_PENDING
    assert payment.link_expires_at == ps.window_cutoff(payer, date(2026, 10, 1))

    parts = urlsplit(payment.link_url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == FORM_URL
    fields = parse_qsl(parts.query, keep_blank_values=True)
    signature = dict(fields).pop("signature")
    unsigned = [(k, v) for k, v in fields if k != "signature"]
    assert ps.prodamus_verify(ps.nest_form(unsigned), KEY, signature)
    assert dict(fields)["order_id"] == f"ap-{payment.id}"
    assert dict(fields)["products[0][price]"] == "13255.00"

    # Повторное нажатие — та же ссылка, второго заказа нет.
    again = ps.get_or_create_payment(db, payer, now, "https://apparchi.ru")
    assert again.id == payment.id
    assert db.query(Payment).count() == 1


def test_price_change_cancels_old_link(db, prices, payer, prodamus):
    now = _msk(2026, 10, 12)
    first = ps.get_or_create_payment(db, payer, now, "https://apparchi.ru")
    db.commit()
    payer.pay_cohort = COHORT_BEFORE  # другая цена
    second = ps.get_or_create_payment(db, payer, now, "https://apparchi.ru")
    db.commit()
    assert second.id != first.id
    assert second.amount_kop == 1275500
    db.refresh(first)
    assert first.status == STATUS_CANCELLED


def test_link_refused_before_window_and_without_price(db, prices, payer, prodamus):
    with pytest.raises(ps.PaymentUnavailable):
        ps.get_or_create_payment(db, payer, _msk(2026, 10, 5), "https://apparchi.ru")
    payer.pay_cohort = ""
    with pytest.raises(ps.PaymentUnavailable):
        ps.get_or_create_payment(db, payer, _msk(2026, 10, 12), "https://apparchi.ru")


def test_link_refused_when_not_configured(db, prices, payer):
    with pytest.raises(ps.PaymentUnavailable):
        ps.get_or_create_payment(db, payer, _msk(2026, 10, 12), "https://apparchi.ru")


# ── Вебхук ───────────────────────────────────────────────────────────────────

def _pending(db, student, period=date(2026, 10, 1), amount=1325500) -> Payment:
    payment = Payment(user_id=student.id, period=period, amount_kop=amount,
                      tariff=student.tariff, status=STATUS_PENDING)
    db.add(payment)
    db.commit()
    return payment


def _post_webhook(client, fields, key=KEY, sign=None):
    if sign is None:
        sign = ps.prodamus_sign(ps.nest_form(fields), key)
    return client.post(
        "/payments/prodamus/webhook",
        data=dict(fields),
        headers={"Sign": sign},
    )


def _fields(payment_id, total="13255.00", order_id="300155", status="success"):
    return [
        ("date", "2026-10-12T12:31:01+03:00"),
        ("order_id", order_id),
        ("order_num", f"ap-{payment_id}"),
        ("domain", "apparchi-test.payform.ru"),
        ("sum", total),
        ("customer_extra", "Ученик #1, Иван/Петров"),
        ("payment_status", status),
        ("products[0][name]", "Обучение Apparchi, октябрь 2026"),
        ("products[0][price]", total),
        ("products[0][quantity]", "1"),
        ("products[0][sum]", total),
    ]


def test_webhook_503_without_key(client):
    resp = client.post("/payments/prodamus/webhook", data={"a": "1"}, headers={"Sign": "x"})
    assert resp.status_code == 503


def test_webhook_rejects_bad_signature(db, client, payer, prodamus):
    payment = _pending(db, payer)
    resp = _post_webhook(client, _fields(payment.id), sign="deadbeef")
    assert resp.status_code == 403
    db.refresh(payment)
    assert payment.status == STATUS_PENDING


def test_webhook_success_pays_and_extends_access(db, client, payer, prodamus, no_delivery):
    payment = _pending(db, payer)
    resp = _post_webhook(client, _fields(payment.id))
    assert resp.status_code == 200, resp.text

    db.refresh(payment)
    db.refresh(payer)
    assert payment.status == STATUS_PAID
    assert payment.prodamus_order_id == "300155"
    assert payment.paid_sum_kop == 1325500
    assert payer.paid_until == ps.paid_until_after(payer, date(2026, 10, 1))
    assert db.query(AuditLog).filter(AuditLog.action == "payment_paid").count() == 1
    titles = [n.title for n in db.query(Notification).filter(Notification.user_id == payer.id)]
    assert "Оплата за октябрь принята" in titles

    # Повтор того же уведомления ничего не меняет.
    again = _post_webhook(client, _fields(payment.id))
    assert again.status_code == 200
    assert db.query(AuditLog).filter(AuditLog.action == "payment_paid").count() == 1


def test_webhook_notifies_care_service(db, client, payer, user_factory, prodamus, no_delivery, monkeypatch):
    care = user_factory(vk_id=200_277, name="Служба заботы")
    monkeypatch.setattr(ps, "PAYMENT_NOTIFY_RECIPIENT_IDS", frozenset({care.id}))
    payment = _pending(db, payer)
    _post_webhook(client, _fields(payment.id))
    texts = [n.text for n in db.query(Notification).filter(Notification.user_id == care.id)]
    assert any("13 255 ₽" in (t or "") for t in texts)


def test_webhook_sum_mismatch_does_not_extend(db, client, payer, prodamus, no_delivery):
    payment = _pending(db, payer)
    resp = _post_webhook(client, _fields(payment.id, total="100.00"))
    assert resp.status_code == 200
    db.refresh(payment)
    db.refresh(payer)
    assert payment.status == STATUS_SUM_MISMATCH
    assert payer.paid_until is None


def test_webhook_unknown_order_is_acknowledged(db, client, payer, prodamus, no_delivery):
    resp = _post_webhook(client, _fields(99999))
    assert resp.status_code == 200
    assert db.query(Payment).count() == 0


def test_webhook_non_success_status_changes_nothing(db, client, payer, prodamus, no_delivery):
    payment = _pending(db, payer)
    resp = _post_webhook(client, _fields(payment.id, status="order_canceled"))
    assert resp.status_code == 200
    db.refresh(payment)
    assert payment.status == STATUS_PENDING


def test_webhook_demo_payment_ignored(db, client, payer, prodamus, no_delivery):
    """Демо-платёж подписан ключом с суффиксом demo — доступ не продлеваем."""
    payment = _pending(db, payer)
    resp = _post_webhook(client, _fields(payment.id), key=KEY + "demo")
    assert resp.status_code == 200
    db.refresh(payment)
    assert payment.status == STATUS_PENDING


def test_webhook_on_cancelled_link_still_counts(db, client, payer, prodamus, no_delivery):
    """Деньги по заменённой ссылке не теряются — засчитываем с пометкой."""
    payment = _pending(db, payer)
    payment.status = STATUS_CANCELLED
    db.commit()
    _post_webhook(client, _fields(payment.id))
    db.refresh(payment)
    assert payment.status == STATUS_PAID
    assert "отменённая" in (payment.note or "")


def test_paid_until_only_grows(db, payer):
    """Оплата старого месяца не укорачивает уже оплаченный срок."""
    later = ps.paid_until_after(payer, date(2026, 12, 1))
    payer.paid_until = later
    payment = _pending(db, payer, period=date(2026, 10, 1))
    ps.record_paid(db, payment, now=_msk(2026, 10, 12), source="prodamus",
                   paid_sum_kop=payment.amount_kop, performed_by_id=payer.id)
    assert payer.paid_until == later


# ── Кабинет ученика ──────────────────────────────────────────────────────────

def test_unpaid_student_locked_only_when_blocking_enabled(db, client, session_factory, payer, monkeypatch):
    payer.paid_until = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    client.cookies.set("session_id", session_factory(payer).id)

    # Флаг выключен (первый месяц) — кабинет открыт.
    assert client.get("/cabinet/learning", follow_redirects=False).status_code == 200

    monkeypatch.setattr(settings, "payments_block_enabled", True)
    resp = client.get("/cabinet/learning", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal"
    assert client.get("/cabinet/personal").status_code == 200


def test_paid_student_not_locked(db, client, session_factory, payer, monkeypatch):
    monkeypatch.setattr(settings, "payments_block_enabled", True)
    payer.paid_until = datetime.now(timezone.utc) + timedelta(days=10)
    db.commit()
    client.cookies.set("session_id", session_factory(payer).id)
    assert client.get("/cabinet/learning", follow_redirects=False).status_code == 200


def test_pay_button_returns_link(db, client, session_factory, prices, payer, prodamus):
    payer.pay_window_start, payer.pay_window_end = 1, 28  # окно открыто в любой день
    db.commit()
    client.cookies.set("session_id", session_factory(payer).id)

    page = client.get("/cabinet/personal")
    assert "data-pay-button" in page.text

    resp = client.post("/cabinet/personal/pay")
    assert resp.status_code == 200
    assert resp.json()["url"].startswith(FORM_URL)


def test_pay_button_hidden_without_window(db, client, session_factory, prices, user_factory, prodamus):
    student = user_factory(tariff="Я С ВАМИ")
    client.cookies.set("session_id", session_factory(student).id)
    assert "data-pay-button" not in client.get("/cabinet/personal").text
    resp = client.post("/cabinet/personal/pay")
    assert resp.status_code == 409
    assert resp.json()["error"]


# ── Настройки оплаты в карточке ученика («Управление») ──────────────────────

def _settings(db, student, by, **overrides):
    values = dict(window_start=10, window_end=15, cohort=COHORT_FROM,
                  price_kop=None, paid_through=date(2026, 10, 1))
    values.update(overrides)
    return ps.apply_payment_settings(db, by.id, student, **values)


def test_settings_paid_month_becomes_next_cutoff(db, user_factory):
    student = user_factory(tariff="Я С ВАМИ")
    assert _settings(db, student, student) is True

    # Оплачен октябрь — срок до отсечки ноябрьского окна 10–15.
    assert student.paid_until == _msk(2026, 11, 16, 9)
    assert ps.paid_month(student) == date(2026, 10, 1)
    db.flush()
    [log] = db.query(AuditLog).filter(AuditLog.action == "payment_settings_change").all()
    assert "оплачено по: — → октябрь 2026" in log.details

    # Повтор тех же значений журнал не засоряет.
    assert _settings(db, student, student) is False


def test_settings_window_change_moves_paid_until(db, user_factory):
    student = user_factory(tariff="Я С ВАМИ")
    _settings(db, student, student)

    _settings(db, student, student, window_start=20, window_end=25)

    assert student.paid_until == _msk(2026, 11, 26, 9)
    assert ps.paid_month(student) == date(2026, 10, 1)


def test_settings_clear_paid_month_and_window(db, user_factory):
    student = user_factory(tariff="Я С ВАМИ")
    _settings(db, student, student, price_kop=1200000)

    _settings(db, student, student, window_start=None, window_end=None, cohort="",
              price_kop=1200000, paid_through=None)

    assert (student.pay_window_start, student.pay_window_end, student.paid_until) == (None, None, None)
    assert student.pay_price_kop == 1200000


@pytest.mark.parametrize("overrides,message", [
    ({"window_start": None, "window_end": None}, "задайте окно"),
    ({"window_start": 16, "window_end": 15}, "Окно оплаты"),
    ({"window_end": None}, "оба дня"),
    ({"price_kop": 0}, "больше нуля"),
    ({"cohort": "июнь"}, "набор"),
])
def test_settings_refuse_bad_values_and_change_nothing(db, user_factory, overrides, message):
    student = user_factory(tariff="Я С ВАМИ")
    with pytest.raises(ps.PaymentSettingsError, match=message):
        _settings(db, student, student, **overrides)
    assert (student.pay_window_start, student.paid_until) == (None, None)


@pytest.mark.parametrize("raw,expected", [("", None), ("2026-10", date(2026, 10, 1))])
def test_parse_paid_month(raw, expected):
    assert ps.parse_paid_month(raw) == expected


@pytest.mark.parametrize("raw", ["октябрь", "2026-13", "2026"])
def test_parse_paid_month_garbage(raw):
    with pytest.raises(ps.PaymentSettingsError):
        ps.parse_paid_month(raw)


@pytest.fixture()
def card(user_factory):
    chief = user_factory(vk_id=970_001, name="Главный", is_admin=True, role_name="суперадмин")
    curator = user_factory(vk_id=970_002, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=970_003, name="Ученик", tariff="Я С ВАМИ")
    student.curator_id = curator.id
    return {"chief": chief, "curator": curator, "student": student}


def _payment_form(**overrides):
    form = {"pay_window_start": "10", "pay_window_end": "15", "pay_cohort": COHORT_FROM,
            "pay_price": "", "paid_month": "2026-10"}
    form.update(overrides)
    return form


def test_card_saves_payment_settings(db, client, session_factory, prices, card):
    student = card["student"]
    db.commit()
    client.cookies.set("session_id", session_factory(card["chief"]).id)

    resp = client.post(f"/cabinet/superadmin/users/{student.id}/payment",
                       data=_payment_form(pay_price="12 000,50"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["pay_price"], body["paid_month"]) == ("12000,50", "2026-10")
    assert body["pay_reference_text"] == "13 255 ₽"
    db.refresh(student)
    assert student.pay_price_kop == 1200050
    assert student.paid_until is not None

    p = client.get(f"/cabinet/students/{student.id}/profile").json()["student"]["manage"]["payment"]
    assert (p["pay_window_start"], p["pay_window_end"], p["pay_cohort"]) == (10, 15, COHORT_FROM)
    assert p["paid_until_text"] == "16.11.2026 в 09:00"


def test_card_refuses_garbage_price_without_changes(db, client, session_factory, card):
    student = card["student"]
    db.commit()
    client.cookies.set("session_id", session_factory(card["chief"]).id)

    resp = client.post(f"/cabinet/superadmin/users/{student.id}/payment",
                       data=_payment_form(pay_price="много"), headers={"Accept": "application/json"})

    assert resp.status_code == 400
    db.refresh(student)
    assert (student.pay_window_start, student.pay_price_kop, student.paid_until) == (None, None, None)


def test_card_payment_curator_forbidden(db, client, session_factory, card):
    db.commit()
    client.cookies.set("session_id", session_factory(card["curator"]).id)

    resp = client.post(f"/cabinet/superadmin/users/{card['student'].id}/payment",
                       data=_payment_form(), headers={"Accept": "application/json"})

    assert resp.status_code == 403


def test_card_payment_archived_read_only(db, client, session_factory, card, user_factory):
    from app.services.user_management import archive_user

    superadmin = user_factory(vk_id=970_004, name="Супер", is_admin=True, role_name="суперадмин")
    db.commit()
    archive_user(db, target_user_id=card["student"].id, performed_by_id=superadmin.id, actor_rank=5)
    client.cookies.set("session_id", session_factory(superadmin).id)

    resp = client.post(f"/cabinet/superadmin/users/{card['student'].id}/payment", data=_payment_form())

    assert resp.status_code == 409


# ── Напоминание в первый день окна (`student_reminders.py`, вид 6) ──────────

def _pay_reminders(db, user_id):
    return [n for n in db.query(Notification).filter(Notification.user_id == user_id)
            if n.title.startswith("Пора оплатить")]


def test_pay_window_reminder_once_on_first_day(db, prices, payer, prodamus):
    from app.services.student_reminders import run_student_reminders

    run_student_reminders(db, _msk(2026, 10, 10, 9, 30))   # окно открылось, но ещё рано
    assert _pay_reminders(db, payer.id) == []

    run_student_reminders(db, _msk(2026, 10, 10, 10, 0))
    run_student_reminders(db, _msk(2026, 10, 12, 10, 0))   # второй раз — тишина
    notes = _pay_reminders(db, payer.id)
    assert [(n.title, n.text) for n in notes] == [(
        "Пора оплатить обучение за октябрь",
        "Сумма – 13 255 ₽. Оплати до 15.10 включительно: кнопка «Оплатить» в «Личной информации».",
    )]


def test_pay_window_reminder_skips_paid_and_unconfigured(db, prices, payer, user_factory, prodamus, monkeypatch):
    from app.services.student_reminders import run_student_reminders

    chief = user_factory(vk_id=970_101, name="Главный", is_admin=True, role_name="админ")
    ps.apply_payment_settings(db, chief.id, payer, window_start=10, window_end=15,
                              cohort=COHORT_FROM, price_kop=None, paid_through=date(2026, 10, 1))
    db.commit()
    run_student_reminders(db, _msk(2026, 10, 10, 12, 0))
    assert _pay_reminders(db, payer.id) == []

    # Без настроек Продамуса кнопки нет — и звать некуда.
    monkeypatch.setattr(settings, "prodamus_secret_key", "")
    run_student_reminders(db, _msk(2026, 11, 10, 12, 0))
    assert _pay_reminders(db, payer.id) == []
