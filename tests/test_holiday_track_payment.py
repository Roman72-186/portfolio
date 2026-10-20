"""Оплата трека каникулярного набора на платформе, «потенциал» и куратор.

План — `plans/2026-10-17-apparchi-оплата-трека.md`. Трек — ссылка группы с ценой;
трек ученика — его тег; оплата ставит тег и срок, возврат снимает тег. Без трека
каникулярному открыта только страница треков. Переписка и напоминания —
переключатели трека.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

import pytest

from app.config import settings
from app.constants import DIALOG_HOLIDAY, PROGRAM_HOLIDAY
from app.models.notification import Notification
from app.models.payment import KIND_HOLIDAY, STATUS_PAID, STATUS_REFUNDED, STATUS_SUM_MISMATCH, Payment
from app.models.tag import Tag, UserTag
from app.services import intake_link as intake_link_service
from app.services import payments as ps
from app.services.tags import add_tag_to_user, get_or_create_tag

KEY = "test-secret-key"
FORM_URL = "https://apparchi-test.payform.ru/"


@pytest.fixture()
def prodamus(monkeypatch):
    monkeypatch.setattr(settings, "prodamus_form_url", FORM_URL)
    monkeypatch.setattr(settings, "prodamus_secret_key", KEY)


@pytest.fixture()
def no_delivery(monkeypatch):
    async def _noop(ids):
        return None
    monkeypatch.setattr("app.api.payments.notify_many", _noop)


def _track(db, *, title="Потенциал", price_kop=90000, active=True, days=14, **flags):
    tag = get_or_create_tag(db, title)
    link = intake_link_service.create_holiday_link(
        db, title=title, tag_id=tag.id,
        access_until=datetime.now(timezone.utc) + timedelta(days=days), created_by_id=None,
    )
    link.price_kop = price_kop
    link.is_active = active
    for key, value in flags.items():
        setattr(link, key, value)
    db.commit()
    return link


def _newcomer(db, user_factory, *, vk_id=310_001, profile_completed=False):
    student = user_factory(vk_id=vk_id, name="Новичок Набора", tariff="",
                           profile_completed=profile_completed)
    student.program = PROGRAM_HOLIDAY
    db.commit()
    return student


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def _tags(db, user):
    return {name for (name,) in db.query(Tag.name).join(UserTag, UserTag.tag_id == Tag.id)
            .filter(UserTag.user_id == user.id)}


def _post_webhook(client, payment, total="900.00", order_id="400155", status="success"):
    fields = [
        ("date", "2026-10-18T12:31:01+03:00"),
        ("order_id", order_id),
        ("order_num", f"ap-{payment.id}"),
        ("sum", total),
        ("payment_status", status),
    ]
    sign = ps.prodamus_sign(ps.nest_form(fields), KEY)
    return client.post("/payments/prodamus/webhook", data=dict(fields), headers={"Sign": sign})


# ── Ворота ───────────────────────────────────────────────────────────────────

def test_without_track_only_track_page_is_open(client, db, user_factory, session_factory):
    _track(db)
    client = _login(client, session_factory, _newcomer(db, user_factory))

    for path in ("/cabinet/learning", "/cabinet/profile", "/cabinet/portfolio"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"] == "/cabinet/personal/track", path

    page = client.get("/cabinet/personal/track")
    assert page.status_code == 200


def test_track_tag_opens_cabinet(client, db, user_factory, session_factory):
    track = _track(db)
    student = _newcomer(db, user_factory)
    add_tag_to_user(db, student.id, track.tag_id)
    db.commit()

    resp = _login(client, session_factory, student).get("/cabinet/profile", follow_redirects=False)
    assert resp.status_code == 200


def test_annual_student_never_sees_track_page(auth_client):
    client, _student = auth_client
    resp = client.get("/cabinet/personal/track", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal"


def test_hidden_tracks_are_not_sold(client, db, user_factory, session_factory):
    closed = _track(db, title="Закрытый", active=False)
    free = _track(db, title="Бесплатный", price_kop=None)
    client = _login(client, session_factory, _newcomer(db, user_factory))
    page = client.get("/cabinet/personal/track")
    assert "Закрытый" not in page.text and "Бесплатный" not in page.text
    for track in (closed, free):
        resp = client.post(f"/cabinet/personal/track/{track.id}/pay", headers={"Accept": "application/json"})
        assert resp.status_code == 409, track.title


# ── Вход по ссылке ───────────────────────────────────────────────────────────

def test_paid_track_link_gives_no_tag_free_one_does(db, user_factory):
    paid = _track(db, title="Платный")
    free = _track(db, title="Свободный", price_kop=None)
    a = user_factory(vk_id=310_011)
    b = user_factory(vk_id=310_012)
    intake_link_service.apply_to_new_user(db, a, paid)
    intake_link_service.apply_to_new_user(db, b, free)
    db.commit()

    assert "Платный" not in _tags(db, a) and a.program == PROGRAM_HOLIDAY
    assert "Свободный" in _tags(db, b)


# ── Ссылка Продамуса и вебхук ────────────────────────────────────────────────

def test_pay_button_builds_track_link(client, db, user_factory, session_factory, prodamus):
    track = _track(db)
    student = _newcomer(db, user_factory)
    client = _login(client, session_factory, student)

    resp = client.post(f"/cabinet/personal/track/{track.id}/pay", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    query = dict(parse_qsl(urlsplit(resp.json()["url"]).query))
    assert query["products[0][name]"] == "Онлайн-курс Потенциал"
    assert query["products[0][type]"] == "course"
    assert query["products[0][price]"] == "900.00"
    assert query["urlSuccess"].endswith("/cabinet/personal/track?paid=1")
    payment = db.query(Payment).filter(Payment.user_id == student.id).one()
    assert (payment.kind, payment.intake_link_id, payment.amount_kop) == (KIND_HOLIDAY, track.id, 90000)

    again = client.post(f"/cabinet/personal/track/{track.id}/pay", headers={"Accept": "application/json"})
    assert again.json()["url"] == resp.json()["url"]
    assert db.query(Payment).filter(Payment.user_id == student.id).count() == 1


@pytest.mark.parametrize("title, name, thanks", [
    ("АРХИпроба 1 трек", "Доступ к материалам АРХИпроба 1 трек",
     "Благодарю за оплату доступа к материалам АРХИпроба 1 трек"),
    ("ЭКЗпроба 1 трек", "Доступ к материалам ЭКЗпроба 1 трек",
     "Благодарю за оплату доступа к материалам ЭКЗпроба 1 трек"),
    ("АРХИпроба 2 трек", "Онлайн-курс АРХИпроба 2 трек", "Благодарю за оплату курса АРХИпроба 2 трек"),
    ("ЭКЗпроба 2 трек", "Онлайн-курс ЭКЗпроба 2 трек", "Благодарю за оплату курса ЭКЗпроба 2 трек"),
    ("Попробуй обучение", "Онлайн-курс Попробуй подготовку", "Благодарю за оплату курса Попробуй подготовку"),
])
def test_track_receipt_wording(db, title, name, thanks):
    """Чек трека дословно по школе 19.10.2026: 1 трек — доступ к материалам,
    2 трек и «Попробуй подготовку» — онлайн-курс."""
    got_name, got_thanks = ps.holiday_receipt(_track(db, title=title))
    assert got_name == name
    assert got_thanks == f"{thanks}\nАдминистратор тг-канала https://t.me/creativeanalytic свяжется с тобой"


def test_closed_track_cannot_be_paid(client, db, user_factory, session_factory, prodamus):
    track = _track(db, active=False)
    client = _login(client, session_factory, _newcomer(db, user_factory))
    resp = client.post(f"/cabinet/personal/track/{track.id}/pay", headers={"Accept": "application/json"})
    assert resp.status_code == 409


def test_webhook_gives_tag_and_access(client, db, user_factory, prodamus, no_delivery):
    track = _track(db)
    student = _newcomer(db, user_factory)
    student.access_until = datetime.now(timezone.utc) + timedelta(days=1)
    payment = ps.get_or_create_track_payment(db, student, track, datetime.now(timezone.utc), "https://x")
    db.commit()

    resp = _post_webhook(client, payment)
    assert resp.status_code == 200
    db.refresh(student)
    db.refresh(payment)
    assert payment.status == STATUS_PAID
    assert "Потенциал" in _tags(db, student)
    assert abs(student.access_until - track.access_until.replace(tzinfo=student.access_until.tzinfo)) < timedelta(seconds=1)
    note = db.query(Notification).filter(Notification.user_id == student.id).one()
    assert "Потенциал" in note.text
    assert student.paid_until is None

    # Повтор того же уведомления ничего не меняет.
    assert _post_webhook(client, payment).status_code == 200
    assert db.query(Notification).filter(Notification.user_id == student.id).count() == 1


def test_webhook_wrong_sum_gives_nothing(client, db, user_factory, prodamus, no_delivery):
    track = _track(db)
    student = _newcomer(db, user_factory)
    payment = ps.get_or_create_track_payment(db, student, track, datetime.now(timezone.utc), "https://x")
    db.commit()

    _post_webhook(client, payment, total="100.00")
    db.refresh(payment)
    assert payment.status == STATUS_SUM_MISMATCH
    assert "Потенциал" not in _tags(db, student)


def test_refund_removes_track_tag(client, db, user_factory, prodamus, no_delivery, admin_user):
    track = _track(db)
    student = _newcomer(db, user_factory)
    payment = ps.get_or_create_track_payment(db, student, track, datetime.now(timezone.utc), "https://x")
    db.commit()
    _post_webhook(client, payment)
    db.refresh(payment)

    ps.revert_paid(db, payment, refund=True, performed_by_id=admin_user.id, now=datetime.now(timezone.utc))
    db.commit()
    assert payment.status == STATUS_REFUNDED
    assert "Потенциал" not in _tags(db, student)
    assert intake_link_service.needs_track(db, student)


# ── Переписка и напоминания по треку ─────────────────────────────────────────

def test_dialog_only_for_track_with_dialog(db, user_factory):
    from app.services.task_block_feedback import _dialog_open_for

    talk = _track(db, title="Потенциал", dialog_enabled=True)
    quiet = _track(db, title="Проба", dialog_enabled=False)
    a = _newcomer(db, user_factory, vk_id=310_021)
    b = _newcomer(db, user_factory, vk_id=310_022)
    add_tag_to_user(db, a.id, talk.tag_id)
    add_tag_to_user(db, b.id, quiet.tag_id)
    db.commit()

    assert _dialog_open_for(db, a, {DIALOG_HOLIDAY}) is True
    assert _dialog_open_for(db, b, {DIALOG_HOLIDAY}) is False
    # Блок не отмечен «Каникулярным набором» — закрыт и «потенциалу».
    assert _dialog_open_for(db, a, {"Я С ВАМИ"}) is False
    annual = user_factory(vk_id=310_023, tariff="Я С ВАМИ")
    assert _dialog_open_for(db, annual, {"Я С ВАМИ"}) is True
    assert _dialog_open_for(db, annual, {DIALOG_HOLIDAY}) is False


def test_dialog_setting_keeps_holiday_mark():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.services.task_blocks import _sync_dialog_settings

    db = MagicMock()
    block = SimpleNamespace(id=1, block_type="upload", dialog_reply_limit=None)
    _sync_dialog_settings(db, block, {"dialog_tariffs": [DIALOG_HOLIDAY, "ЧУЖОЕ"]})
    added = [call.args[0].tariff for call in db.add.call_args_list]
    assert added == [DIALOG_HOLIDAY]


def test_reminders_only_for_track_with_reminders(db, user_factory):
    from app.services.student_reminders import _without_silent_holiday

    loud = _track(db, title="Потенциал", reminders_enabled=True)
    quiet = _track(db, title="Проба")
    a = _newcomer(db, user_factory, vk_id=310_031)
    b = _newcomer(db, user_factory, vk_id=310_032)
    c = _newcomer(db, user_factory, vk_id=310_033)  # без трека
    annual = user_factory(vk_id=310_034)
    add_tag_to_user(db, a.id, loud.tag_id)
    add_tag_to_user(db, b.id, quiet.tag_id)
    db.commit()

    kept = _without_silent_holiday(db, {u.id: u for u in (a, b, c, annual)})
    assert set(kept) == {a.id, annual.id}


# ── Экран школы и куратор ────────────────────────────────────────────────────

def test_track_settings_saved_from_periods_screen(admin_client, db):
    client, _admin = admin_client
    track = _track(db, price_kop=None)

    resp = client.post(f"/cabinet/intake/{track.slug}/track", data={
        "csrf_token": "x", "price": "1 500", "description": "Неделя с куратором",
        "reminders": "1", "dialog": "1",
    }, follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(track)
    assert (track.price_kop, track.description, track.reminders_enabled, track.dialog_enabled) == (
        150000, "Неделя с куратором", True, True,
    )
    assert client.get("/cabinet/periods").status_code == 200


def test_entry_link_created_once(admin_client, db):
    client, _admin = admin_client
    client.post("/cabinet/intake/entry", data={"csrf_token": "x"}, follow_redirects=False)
    client.post("/cabinet/intake/entry", data={"csrf_token": "x"}, follow_redirects=False)
    entry = intake_link_service.entry_link(db)
    assert entry is not None and entry.tag_id is None and entry.is_active is False
    assert len([link for link in intake_link_service.holiday_links(db) if link.tag_id is None]) == 1


def test_curator_sees_own_holiday_students_in_filter(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=310_041, role_name="куратор")
    mine = _newcomer(db, user_factory, vk_id=310_042, profile_completed=True)
    mine.name = "Мой Каникулярный"
    other = _newcomer(db, user_factory, vk_id=310_043, profile_completed=True)
    other.name = "Чужой Каникулярный"
    mine.curator_id = curator.id
    db.commit()
    client = _login(client, session_factory, curator)

    assert "Мой Каникулярный" not in client.get("/cabinet/students").text
    page = client.get("/cabinet/students?program=holiday")
    assert "Мой Каникулярный" in page.text
    assert "Чужой Каникулярный" not in page.text


# ── Второй трек после купленного (школа 18.10.2026) ─────────────────────────

def test_second_track_can_be_bought_same_cannot(client, db, user_factory, session_factory, prodamus):
    first = _track(db, title="АРХИпроба 1 трек", price_kop=125500)
    second = _track(db, title="ЭКЗпроба 1 трек", price_kop=125500)
    student = _newcomer(db, user_factory)
    add_tag_to_user(db, student.id, first.tag_id)
    db.commit()
    client = _login(client, session_factory, student)

    assert client.get("/cabinet/personal/track").status_code == 200

    same = client.post(f"/cabinet/personal/track/{first.id}/pay", headers={"Accept": "application/json"})
    assert same.status_code == 409 and "уже оплачен" in same.json()["error"]
    other = client.post(f"/cabinet/personal/track/{second.id}/pay", headers={"Accept": "application/json"})
    assert other.status_code == 200 and other.json()["url"]


def test_any_track_opens_dialog_and_reminders(db, user_factory):
    from app.services.student_reminders import _without_silent_holiday
    from app.services.task_block_feedback import _dialog_open_for

    plain = _track(db, title="АРХИпроба 1 трек")
    potential = _track(db, title="Потенциал", dialog_enabled=True, reminders_enabled=True)
    student = _newcomer(db, user_factory)
    add_tag_to_user(db, student.id, plain.tag_id)
    db.commit()
    assert _dialog_open_for(db, student, {DIALOG_HOLIDAY}) is False
    assert student.id not in _without_silent_holiday(db, {student.id: student})

    add_tag_to_user(db, student.id, potential.tag_id)
    db.commit()
    assert _dialog_open_for(db, student, {DIALOG_HOLIDAY}) is True
    assert student.id in _without_silent_holiday(db, {student.id: student})


def test_paying_one_track_keeps_open_link_of_another(client, db, user_factory, prodamus, no_delivery):
    """Оплата одного трека не гасит ссылку на другой: иначе её оплата пришла бы
    «по отменённой ссылке» с ложной тревогой службе заботы (проверка 19.10.2026)."""
    from app.models.payment import STATUS_PENDING

    first = _track(db, title="АРХИпроба 1 трек", price_kop=125500)
    second = _track(db, title="ЭКЗпроба 1 трек", price_kop=125500)
    student = _newcomer(db, user_factory)
    now = datetime.now(timezone.utc)
    pay_first = ps.get_or_create_track_payment(db, student, first, now, "https://x")
    pay_second = ps.get_or_create_track_payment(db, student, second, now, "https://x")
    db.commit()

    _post_webhook(client, pay_first, total="1255.00")
    db.refresh(pay_second)
    assert pay_second.status == STATUS_PENDING
    _post_webhook(client, pay_second, total="1255.00", order_id="400156")
    db.refresh(pay_second)
    assert pay_second.status == STATUS_PAID
    assert {"АРХИпроба 1 трек", "ЭКЗпроба 1 трек"} <= _tags(db, student)
    staff_titles = [n.title for n in db.query(Notification).filter(Notification.user_id != student.id)]
    assert "Оплата по отменённой ссылке" not in staff_titles


# ── Короткий адрес набора (19.10.2026) ──────────────────────────────────────

def test_short_entry_path_leads_to_entry_link(client, db, monkeypatch):
    from app.constants import HOLIDAY_ENTRY_PATH

    assert client.get(HOLIDAY_ENTRY_PATH, follow_redirects=False).status_code == 404
    entry = intake_link_service.create_entry_link(
        db, access_until=datetime.now(timezone.utc) + timedelta(days=5), created_by_id=None,
    )
    db.commit()
    # Выключена — экран «Набор закрыт», как у /join/<адрес>.
    closed = client.get(HOLIDAY_ENTRY_PATH, follow_redirects=False)
    assert closed.status_code == 200

    entry.is_active = True
    db.commit()
    monkeypatch.setattr("app.api.auth._telegram_login_enabled", lambda: True)
    short = client.get(HOLIDAY_ENTRY_PATH, follow_redirects=False)
    long = client.get(f"/join/{entry.slug}", follow_redirects=False)
    assert short.status_code == long.status_code == 302
    assert short.headers["location"].split("?")[0] == long.headers["location"].split("?")[0]


# ── Тестовый проход служебного аккаунта (19.10.2026) ────────────────────────

def test_service_account_opens_track_without_payment(client, db, user_factory, session_factory, monkeypatch):
    from app.constants import REPORT_EXCLUDED_USER_IDS
    from app.models.audit_log import AuditLog

    track = _track(db, title="АРХИпроба 1 трек", price_kop=125500)
    student = _newcomer(db, user_factory)
    monkeypatch.setattr("app.constants.REPORT_EXCLUDED_USER_IDS", frozenset(REPORT_EXCLUDED_USER_IDS) | {student.id})
    monkeypatch.setattr("app.services.payments.REPORT_EXCLUDED_USER_IDS", frozenset(REPORT_EXCLUDED_USER_IDS) | {student.id})
    client = _login(client, session_factory, student)

    assert client.get("/cabinet/personal/track").status_code == 200
    resp = client.post(f"/cabinet/personal/track/{track.id}/pay", headers={"Accept": "application/json"})
    assert resp.status_code == 200 and resp.json()["url"] == "/cabinet/personal/track?test=1"
    assert "АРХИпроба 1 трек" in _tags(db, student)
    assert db.query(Payment).filter(Payment.user_id == student.id).count() == 0
    assert db.query(AuditLog).filter(AuditLog.action == "holiday_track_test_grant").count() == 1
    assert client.get("/cabinet/profile", follow_redirects=False).status_code == 200


def test_regular_student_still_goes_to_prodamus(client, db, user_factory, session_factory, prodamus):
    track = _track(db, title="АРХИпроба 1 трек", price_kop=125500)
    student = _newcomer(db, user_factory)
    client = _login(client, session_factory, student)
    resp = client.post(f"/cabinet/personal/track/{track.id}/pay", headers={"Accept": "application/json"})
    assert resp.json()["url"].startswith("https://apparchi-test.payform.ru/")
    assert "АРХИпроба 1 трек" not in _tags(db, student)


# ── Ожидание открытия программы (служба заботы 19.10.2026) ─────────────────

OPENS_LATER = datetime(2099, 10, 24, 21, 0, tzinfo=timezone.utc)  # 25.10.2099 00:00 МСК


def _paid_newcomer(db, user_factory, *, vk_id=310_050, profile_completed=True):
    track = _track(db)
    student = _newcomer(db, user_factory, vk_id=vk_id, profile_completed=profile_completed)
    add_tag_to_user(db, student.id, track.tag_id)
    db.commit()
    return student


def test_paid_student_waits_for_program_opening(client, db, user_factory, session_factory, monkeypatch):
    monkeypatch.setattr("app.constants.HOLIDAY_PROGRAM_OPENS_AT", OPENS_LATER)
    client = _login(client, session_factory, _paid_newcomer(db, user_factory))

    page = client.get("/cabinet/learning", follow_redirects=False)
    assert page.status_code == 200

    for path in (
        "/cabinet/portfolio", "/cabinet/learning/archive", "/cabinet/tracker/tasks/1/blocks",
        "/cabinet/videos/1", "/upload",
    ):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"] == "/cabinet/learning", path

    assert client.get("/cabinet/personal/contacts", follow_redirects=False).status_code == 200
    assert client.get("/cabinet/personal/track").status_code == 200


def test_waiting_student_can_fill_profile(client, db, user_factory, session_factory, monkeypatch):
    monkeypatch.setattr("app.constants.HOLIDAY_PROGRAM_OPENS_AT", OPENS_LATER)
    student = _paid_newcomer(db, user_factory, profile_completed=False)
    client = _login(client, session_factory, student)
    assert client.get("/cabinet/profile", follow_redirects=False).status_code == 200
    resp = client.get("/cabinet/learning", follow_redirects=False)
    assert resp.headers["location"] == "/cabinet/profile"


def test_program_opens_after_the_date(client, db, user_factory, session_factory):
    client = _login(client, session_factory, _paid_newcomer(db, user_factory))
    assert client.get("/cabinet/learning", follow_redirects=False).status_code == 200
    assert client.get("/cabinet/portfolio", follow_redirects=False).status_code == 200


def test_unpaid_student_still_goes_to_track_page(client, db, user_factory, session_factory, monkeypatch):
    monkeypatch.setattr("app.constants.HOLIDAY_PROGRAM_OPENS_AT", OPENS_LATER)
    _track(db)
    client = _login(client, session_factory, _newcomer(db, user_factory))
    resp = client.get("/cabinet/learning", follow_redirects=False)
    assert resp.headers["location"] == "/cabinet/personal/track"


def test_service_account_does_not_wait(client, db, user_factory, session_factory, monkeypatch):
    from app.constants import REPORT_EXCLUDED_USER_IDS

    monkeypatch.setattr("app.constants.HOLIDAY_PROGRAM_OPENS_AT", OPENS_LATER)
    student = _paid_newcomer(db, user_factory)
    monkeypatch.setattr("app.constants.REPORT_EXCLUDED_USER_IDS", frozenset(REPORT_EXCLUDED_USER_IDS) | {student.id})
    client = _login(client, session_factory, student)
    assert client.get("/cabinet/portfolio", follow_redirects=False).status_code == 200


def test_annual_student_does_not_wait(auth_client, monkeypatch):
    monkeypatch.setattr("app.constants.HOLIDAY_PROGRAM_OPENS_AT", OPENS_LATER)
    client, _student = auth_client
    assert client.get("/cabinet/portfolio", follow_redirects=False).status_code == 200
