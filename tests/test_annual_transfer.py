"""Новые ученики одним списком и переход каникулярного в годовой курс оплатой.

Владелец 26.10.2026: школа присылает списки новичков и каникулярного набора с
тарифом, суммой, окном и первым месяцем оплаты; ссылка приходит сама в первый
день окна, а каникулярный после оплаты месяца сам переходит в годовой курс
(«все после оплаты в месячный и на годовой курс попадут»). Переход —
`user_management.move_to_annual`, его зовёт только `payments.record_paid`.
Загрузка — `services/payment_import.py`, колонки «Ник», «Окно» и «Первый
месяц оплаты».
"""
import io
from datetime import date, datetime, timedelta, timezone
from itertools import count

import openpyxl
import pytest

from app.constants import ANNUAL_NEWCOMER_TAG, PROGRAM_ANNUAL, PROGRAM_HOLIDAY
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.payment import COHORT_FROM, KIND_HOLIDAY, STATUS_PENDING, Payment, PaymentPrice
from app.models.student_reminder import KIND_PAY_WINDOW
from app.models.tag import Tag, UserTag
from app.services import payment_import as pi
from app.services import payments as ps
from app.services import student_reminders
from app.services.review_aggregate import _accessible_students
from app.services.tracker import cycle_debt
from app.services.tz import MSK_TZ, today_msk
from app.services.video_topics import accessible_topic_ids

from tests.test_program_access_from import _topic, _undated_task

TODAY = today_msk()
HEADER = ["Ник", "Имя", "Фамилия", "Тариф", "Сумма", "Окно", "Первый месяц оплаты"]
OCT_26 = date(2026, 10, 26)

_VK_IDS = count(940_001)


def _msk(*args) -> datetime:
    return datetime(*args, tzinfo=MSK_TZ).astimezone(timezone.utc)


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _rows(rows, today=OCT_26):
    return pi.parse_workbook(_xlsx([HEADER, *rows]), today)


def _preview(db, rows, today=OCT_26):
    return {r.row.line: r for r in pi.build_preview(db, _rows(rows, today))}


@pytest.fixture()
def prices(db):
    db.add_all([
        PaymentPrice(tariff="Я С ВАМИ", cohort=COHORT_FROM, amount_kop=1325500),
        PaymentPrice(tariff="Я САМ", cohort=COHORT_FROM, amount_kop=677500),
    ])
    db.commit()


@pytest.fixture()
def chief(user_factory):
    return user_factory(vk_id=940_000, name="Главный", is_admin=True, role_name="админ")


@pytest.fixture()
def make_student(db, user_factory):
    def _make(first, last, *, nick=None, tariff="Я С ВАМИ", holiday=False, **fields):
        student = user_factory(vk_id=next(_VK_IDS), name=f"{first} {last}", tariff=tariff)
        student.first_name, student.last_name = first, last
        student.tg_username = nick
        if holiday:
            student.program = PROGRAM_HOLIDAY
            student.tariff = ""
            student.access_until = datetime.now(timezone.utc) + timedelta(days=5)
        for key, value in fields.items():
            setattr(student, key, value)
        db.commit()
        return student
    return _make


# ── Разбор колонок ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("10–15", (10, 15)), ("10-15", (10, 15)), ("с 25 по 30", (25, 30)), ("", None),
    # Excel превращает набранное «10-15» в 15 октября.
    (datetime(2026, 10, 15), (10, 15)),
])
def test_parse_window(raw, expected):
    assert pi.parse_window(raw) == expected


@pytest.mark.parametrize("raw", ["10", "15–10", "40–45"])
def test_parse_window_refuses(raw):
    with pytest.raises(ValueError):
        pi.parse_window(raw)


@pytest.mark.parametrize("raw, expected", [
    ("ноябрь", date(2026, 11, 1)),
    ("Ноября 2027", date(2027, 11, 1)),
    ("октябрь", date(2026, 10, 1)),      # текущий месяц — этот год
    ("сентябрь", date(2026, 9, 1)),      # прошлый месяц — этот год
    ("август", date(2027, 8, 1)),        # раньше — следующий год
    ("11.2026", date(2026, 11, 1)),
    ("2026-12", date(2026, 12, 1)),
    ("01.12.2026", date(2026, 12, 1)),
    (datetime(2026, 11, 20), date(2026, 11, 1)),
    ("", None),
])
def test_parse_month(raw, expected):
    assert pi.parse_month(raw, OCT_26) == expected


@pytest.mark.parametrize("raw", ["скоро", "13.2026"])
def test_parse_month_refuses(raw):
    with pytest.raises(ValueError):
        pi.parse_month(raw, OCT_26)


def test_sum_per_month_column_is_amount_not_month():
    rows = pi.parse_workbook(_xlsx([["Имя", "Фамилия", "Сумма в месяц"], ["Анна", "Иванова", 13255]]))
    assert (rows[0].amount_kop, rows[0].first_month) == (1325500, None)


def test_nick_alone_is_enough_and_bad_cells_mark_row():
    rows = _rows([
        ["@Ivan_K", "", "", "Я с вами", 13255, "10–15", "ноябрь"],
        ["@anna", "", "", "Я с вами", 13255, "когда-нибудь", "ноябрь"],
    ])
    assert (rows[0].nick, rows[0].error, rows[0].paid_through) == ("ivan_k", None, date(2026, 10, 1))
    assert "Не разобрать окно" in rows[1].error


# ── Предпросмотр и запись ────────────────────────────────────────────────────

def test_row_window_and_month_open_button_in_first_window(db, prices, chief, make_student):
    anna = make_student("Анна", "Иванова", nick="anna_i")
    rows = _rows([["anna_i", "", "", "Я с вами", 13255, "10-15", "ноябрь"]])

    pi.apply_import(db, chief.id, rows, {2: anna.id})
    db.commit()
    db.refresh(anna)

    assert (anna.pay_window_start, anna.pay_window_end, anna.pay_cohort) == (10, 15, COHORT_FROM)
    assert ps.paid_month(anna) == date(2026, 10, 1)
    assert ps.due_period(anna, _msk(2026, 11, 1)) == date(2026, 11, 1)
    # «Оплатить» — в первый день окна месяца, не раньше.
    assert not ps.can_pay_now(anna, _msk(2026, 11, 9, 23, 59))
    assert ps.can_pay_now(anna, _msk(2026, 11, 10, 0, 1))


def test_repeat_upload_changes_nothing_and_keeps_card_window(db, prices, chief, make_student):
    anna = make_student("Анна", "Иванова", nick="anna_i")
    rows = [["@ANNA_I", "", "", "Я с вами", 13255, "10–15", "ноябрь"]]
    pi.apply_import(db, chief.id, _rows(rows), {2: anna.id})
    db.commit()

    again = _preview(db, rows)[2]
    other_window = _preview(db, [["anna_i", "", "", "Я с вами", 13255, "25–30", "декабрь"]])[2]

    assert again.status == pi.ROW_SAME
    assert other_window.status == pi.ROW_SAME
    assert "Окно у ученика уже стоит: 10–15" in other_window.note


def test_nick_first_then_name_and_not_found(db, prices, make_student):
    make_student("Анна", "Иванова", nick="anna_i")
    make_student("Анна", "Иванова", nick="anna_two")

    rows = _preview(db, [
        ["anna_two", "Анна", "Иванова", "Я с вами", 13255, "10–15", "ноябрь"],
        ["", "Анна", "Иванова", "Я с вами", 13255, "10–15", "ноябрь"],
        ["typo_nick", "Анна", "Иванова", "Я с вами", 13255, "10–15", "ноябрь"],
        ["nobody_yet", "", "", "Я с вами", 13255, "10–15", "ноябрь"],
    ])

    # По нику — точно, без однофамильцев; по имени — двое.
    assert rows[2].status == pi.ROW_READY and rows[2].student.tg_username == "anna_two"
    assert rows[3].status == pi.ROW_AMBIGUOUS
    assert rows[4].status == pi.ROW_AMBIGUOUS and "не нашёлся" in rows[4].note
    assert rows[5].status == pi.ROW_NOT_FOUND and "ещё не заходил" in rows[5].note


def test_holiday_row_sets_tariff_after_payment_and_own_price(db, prices, chief, make_student):
    kid = make_student("Илья", "Петров", nick="ilya_p", holiday=True)

    row = _preview(db, [["ilya_p", "", "", "Я с вами", 13255, "10–15", "ноябрь"]])[2]
    assert row.status == pi.ROW_READY
    assert "тариф после оплаты: – → Я с вами" in row.changes
    assert pi.HOLIDAY_NOTE in row.note

    pi.apply_import(db, chief.id, _rows([["ilya_p", "", "", "Я с вами", 13255, "10–15", "ноябрь"]]), {2: kid.id})
    db.commit()
    db.refresh(kid)
    assert kid.pay_next_tariff == "Я С ВАМИ"
    # Цена по справочнику считается от текущего тарифа, а у каникулярного он
    # пустой — без своей цены ссылку не из чего было бы собрать.
    assert kid.pay_price_kop == 1325500
    assert ps.price_kop(db, kid) == 1325500
    assert kid.program == PROGRAM_HOLIDAY


def test_holiday_row_without_tariff_and_with_legacy_one(db, prices, make_student):
    make_student("Илья", "Петров", nick="ilya_p", holiday=True)
    make_student("Ира", "Сидорова", nick="ira_s", holiday=True)

    rows = _preview(db, [
        ["ilya_p", "", "", "", 13255, "10–15", "ноябрь"],
        ["ira_s", "", "", "Уверенный", 13255, "10–15", "ноябрь"],
    ])

    assert rows[2].status == pi.ROW_READY and "выберет его в анкете" in rows[2].note
    assert rows[3].status == pi.ROW_ERROR and "больше не продаётся" in rows[3].note


# ── Переход оплатой месяца ───────────────────────────────────────────────────

def _holiday_payer(db, make_student, chief, **fields):
    kid = make_student("Илья", "Петров", nick="ilya_p", holiday=True, **fields)
    track_tag = Tag(name="АРХИпроба 1 трек")
    db.add(track_tag)
    db.flush()
    db.add(UserTag(user_id=kid.id, tag_id=track_tag.id))
    ps.apply_payment_settings(db, chief.id, kid, window_start=10, window_end=15, cohort=COHORT_FROM,
                              price_kop=1325500, paid_through=date(2026, 10, 1), next_tariff="Я С ВАМИ")
    db.commit()
    return kid, track_tag


def _mark(db, kid, chief, period=date(2026, 11, 1)):
    payment, _ = ps.mark_paid_manually(db, kid, period=period, amount_kop=1325500, paid_on=date(2026, 11, 10),
                                       comment="", performed_by_id=chief.id)
    db.commit()
    db.refresh(kid)
    return payment


def test_month_payment_moves_holiday_student_to_annual(db, prices, chief, make_student, monkeypatch):
    monkeypatch.setattr(ps, "PAYMENT_NOTIFY_RECIPIENT_IDS", {chief.id})
    kid, track_tag = _holiday_payer(db, make_student, chief)
    before = datetime.now(timezone.utc)

    payment = _mark(db, kid, chief)

    assert kid.program == PROGRAM_ANNUAL
    assert kid.access_until is None
    assert kid.profile_completed is False
    assert kid.tariff == "Я С ВАМИ" and kid.pay_next_tariff == ""
    assert ps.paid_month(kid) == date(2026, 11, 1)
    from_ = kid.program_access_from.replace(tzinfo=timezone.utc) if kid.program_access_from.tzinfo is None \
        else kid.program_access_from
    assert from_ >= before
    tag_names = {name for (name,) in db.query(Tag.name).join(UserTag, UserTag.tag_id == Tag.id)
                 .filter(UserTag.user_id == kid.id)}
    # Тег «Новенькие» как у нового годового, тег трека остаётся историей.
    assert {ANNUAL_NEWCOMER_TAG, track_tag.name} <= tag_names
    log = db.query(AuditLog).filter(AuditLog.action == "program_change", AuditLog.target_user_id == kid.id).one()
    assert f"оплата #{payment.id}" in log.details

    to_student = db.query(Notification).filter(Notification.user_id == kid.id,
                                               Notification.title == "Оплата принята").one()
    to_staff = db.query(Notification).filter(Notification.user_id == chief.id).one()
    assert ps.MOVED_TO_ANNUAL_STUDENT_TEXT in to_student.text
    assert ps.MOVED_TO_ANNUAL_STAFF_TEXT in to_staff.text
    assert "Тариф: Я с вами" in to_staff.text


def test_webhook_payment_moves_too(db, client, prices, chief, make_student, monkeypatch):
    from tests.test_payments import KEY, FORM_URL, _fields, _post_webhook
    from app.config import settings

    monkeypatch.setattr(settings, "prodamus_form_url", FORM_URL)
    monkeypatch.setattr(settings, "prodamus_secret_key", KEY)

    async def _noop(ids):
        return None
    monkeypatch.setattr("app.api.payments.notify_many", _noop)
    kid, _ = _holiday_payer(db, make_student, chief)
    payment = Payment(user_id=kid.id, period=date(2026, 11, 1), amount_kop=1325500,
                      tariff=ps.tariff_to_pay(kid), status=STATUS_PENDING)
    db.add(payment)
    db.commit()

    resp = _post_webhook(client, _fields(payment.id))

    assert resp.status_code == 200, resp.text
    db.refresh(kid)
    assert (kid.program, kid.tariff) == (PROGRAM_ANNUAL, "Я С ВАМИ")


def test_refund_keeps_annual_and_annual_or_track_payment_does_not_move(db, prices, chief, make_student):
    kid, _ = _holiday_payer(db, make_student, chief)
    payment = _mark(db, kid, chief)

    ps.revert_paid(db, payment, refund=True, performed_by_id=chief.id, now=_msk(2026, 11, 11))
    db.commit()
    db.refresh(kid)
    assert kid.program == PROGRAM_ANNUAL

    annual = make_student("Анна", "Иванова", pay_window_start=10, pay_window_end=15, pay_cohort=COHORT_FROM)
    _mark(db, annual, chief)
    assert db.query(AuditLog).filter(AuditLog.action == "program_change",
                                     AuditLog.target_user_id == annual.id).count() == 0

    other = make_student("Ира", "Сидорова", nick="ira_s", holiday=True)
    track = Payment(user_id=other.id, period=date(2026, 10, 1), kind=KIND_HOLIDAY, amount_kop=125500,
                    status=STATUS_PENDING)
    db.add(track)
    db.flush()
    ps.record_paid(db, track, now=datetime.now(timezone.utc), source=ps.SOURCE_MANUAL,
                   paid_sum_kop=125500, performed_by_id=chief.id)
    db.commit()
    db.refresh(other)
    assert other.program == PROGRAM_HOLIDAY


def test_moved_student_sees_only_current_program_without_debt(db, prices, chief, make_student, regular_user):
    """Закончившееся до перехода не видно и долгом не считается: после 01.11
    переведённый идёт по общей программе (Александрия 26.10.2026)."""
    ended = _topic(db, regular_user, title="3 неделя",
                   starts_on=TODAY - timedelta(days=14), ends_on=TODAY - timedelta(days=1))
    _undated_task(db, regular_user, ended, title="Задание прошлой недели")
    current = _topic(db, regular_user, title="1 неделя ноября",
                     starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=6))
    _undated_task(db, regular_user, current, title="Задание этой недели")
    kid, _ = _holiday_payer(db, make_student, chief)

    _mark(db, kid, chief)

    visible = accessible_topic_ids(db, kid.id)
    assert current.id in visible and ended.id not in visible
    assert cycle_debt(db, kid.id, TODAY) is None
    # Точка А и «Проверка по ученику» — список, откуда каникулярные исключены.
    staff = {"user_id": chief.id, "role_rank": 4}
    assert kid.id in {s.id for s in _accessible_students(db, staff)}


def test_moved_student_opens_annual_questionnaire_with_tariff_step(db, client, prices, chief, make_student,
                                                                   session_factory):
    kid, _ = _holiday_payer(db, make_student, chief)
    _mark(db, kid, chief)
    client.cookies.set("session_id", session_factory(kid).id)

    page = client.get("/cabinet/profile")

    assert page.status_code == 200, page.text
    assert "Тариф обучения" in page.text


# ── Напоминание об окне ──────────────────────────────────────────────────────

def test_pay_window_reminder_reaches_holiday_after_track_ended(db, prices, chief, make_student, monkeypatch):
    """Срок трека к окну уже прошёл, а у трека выключены напоминания — об
    окне оплаты месяца ученик всё равно узнаёт."""
    monkeypatch.setattr(ps, "payments_configured", lambda: True)
    kid, _ = _holiday_payer(db, make_student, chief, access_until=_msk(2026, 11, 2))
    at = _msk(2026, 11, 10, 12, 0)

    items = student_reminders.collect(db, at)

    assert KIND_PAY_WINDOW in {item.kind for item in items.get(kid.id, [])}


# ── Дорога к загрузке у Александрии ──────────────────────────────────────────

def test_named_moderator_reaches_import_from_payments(db, client, session_factory, user_factory, monkeypatch):
    """Прод 26.10.2026: Александрии (#278) открыта правка учеников, а раздел
    «Люди» — нет, и вкладка «Оплата списком» в «Людях» ей не видна. Ссылка —
    на экране «Оплаты», обратная со страницы загрузки — туда же."""
    from app.models.section_access import SectionAccessRule

    named = user_factory(vk_id=940_900, name="Александрия", role_name="модератор")
    monkeypatch.setattr(ps, "PAYMENTS_EXTRA_USER_IDS", frozenset({named.id}))
    db.add_all([
        SectionAccessRule(section_key="people", user_id=named.id, level="none"),
        SectionAccessRule(section_key="people:students", user_id=named.id, level="edit"),
    ])
    db.commit()
    client.cookies.set("session_id", session_factory(named).id)

    board = client.get("/cabinet/superadmin/payments?month=2026-10")
    page = client.get("/cabinet/superadmin/payment-import")

    assert board.status_code == 200 and 'href="/cabinet/superadmin/payment-import"' in board.text
    assert page.status_code == 200 and 'id="payImportFile"' in page.text
    assert 'href="/cabinet/superadmin/payments" class="back-link"' in page.text


def test_month_without_year_in_november_keeps_october_this_year():
    assert pi.parse_month("октябрь", date(2026, 11, 2)) == date(2026, 10, 1)
    assert pi.parse_month("январь", date(2026, 11, 2)) == date(2027, 1, 1)


# ── Первая оплата — сразу (школа 27.10.2026) ─────────────────────────────────

def _fresh_holiday(db, make_student, chief, window=(25, 30)):
    """Каникулярный, которому только что задали окно и цену — оплат не было."""
    kid = make_student("Илья", "Петров", nick="ilya_p", holiday=True, access_until=_msk(2026, 11, 2))
    ps.apply_payment_settings(db, chief.id, kid, window_start=window[0], window_end=window[1], cohort="",
                              price_kop=1325500, paid_through=None, next_tariff="Я С ВАМИ")
    db.commit()
    return kid


def test_first_payment_is_open_right_away_then_by_window(db, prices, chief, make_student, monkeypatch):
    monkeypatch.setattr(ps, "payments_configured", lambda: True)
    kid = _fresh_holiday(db, make_student, chief)
    nov_2 = _msk(2026, 11, 2, 12, 0)

    # Первая оплата до окна — за текущий месяц, следующая только в декабре.
    assert ps.due_period(kid, nov_2) == date(2026, 11, 1)
    assert ps.can_pay_now(kid, nov_2)
    view = ps.student_payment_view(db, kid, nov_2)
    assert view["first"] and not view["overdue"] and view["window_text"] == "с 25 по 30 число"

    payment, _ = ps.mark_paid_manually(db, kid, period=ps.due_period(kid, nov_2), amount_kop=1325500,
                                       paid_on=date(2026, 11, 2), comment="", performed_by_id=chief.id)
    db.commit()
    db.refresh(kid)

    assert kid.program == PROGRAM_ANNUAL
    # Следующая оплата — в своё окно 25–30 декабря.
    assert ps.due_period(kid, _msk(2026, 11, 20)) == date(2026, 12, 1)
    assert not ps.can_pay_now(kid, _msk(2026, 11, 25, 0, 1))
    assert not ps.can_pay_now(kid, _msk(2026, 12, 24, 23, 0))
    assert ps.can_pay_now(kid, _msk(2026, 12, 25, 0, 1))


def test_first_payment_inside_open_window_is_current_month(db, prices, chief, make_student):
    kid = _fresh_holiday(db, make_student, chief, window=(10, 15))
    assert ps.due_period(kid, _msk(2026, 11, 12)) == date(2026, 11, 1)
    assert ps.due_period(kid, _msk(2026, 11, 2)) == date(2026, 11, 1)


def test_november_link_reused_before_window_and_next_payment_in_december(
    db, prices, chief, make_student, monkeypatch,
):
    from tests.test_payments import KEY, FORM_URL
    from app.config import settings

    monkeypatch.setattr(settings, "prodamus_form_url", FORM_URL)
    monkeypatch.setattr(settings, "prodamus_secret_key", KEY)
    kid = _fresh_holiday(db, make_student, chief, window=(10, 15))
    # Как у ученика 308: ноябрьская ссылка уже создана заранее.
    existing = ps.get_or_create_payment(db, kid, _msk(2026, 11, 10), "https://example.test")
    db.commit()
    payment = ps.get_or_create_payment(db, kid, _msk(2026, 11, 6), "https://example.test")
    assert payment.id == existing.id
    assert payment.period == date(2026, 11, 1)
    assert db.query(Payment).filter(Payment.user_id == kid.id).count() == 1
    ps.record_paid(db, payment, now=_msk(2026, 11, 6), source=ps.SOURCE_PRODAMUS,
                   paid_sum_kop=payment.amount_kop, performed_by_id=chief.id)
    db.commit()
    assert kid.program == PROGRAM_ANNUAL
    assert kid.paid_until == _msk(2026, 12, 16, 2)
    assert ps.due_period(kid, _msk(2026, 11, 6)) == date(2026, 12, 1)
    assert not ps.can_pay_now(kid, _msk(2026, 12, 9, 23, 59))
    assert ps.can_pay_now(kid, _msk(2026, 12, 10))


def test_closed_track_page_shows_first_payment_button(db, client, prices, chief, make_student, session_factory,
                                                      monkeypatch):
    monkeypatch.setattr(ps, "payments_configured", lambda: True)
    kid = _fresh_holiday(db, make_student, chief)
    kid.access_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    client.cookies.set("session_id", session_factory(kid).id)

    page = client.get("/cabinet/personal")

    assert page.status_code == 200
    assert "Оплатить 13 255 ₽" in page.text


def test_first_payment_text_when_cabinet_open(db, client, prices, chief, make_student, session_factory,
                                              monkeypatch):
    monkeypatch.setattr(ps, "payments_configured", lambda: True)
    kid = _fresh_holiday(db, make_student, chief)
    kid.access_until = datetime.now(timezone.utc) + timedelta(days=3)
    db.commit()
    client.cookies.set("session_id", session_factory(kid).id)

    page = client.get("/cabinet/personal")

    assert page.status_code == 200
    assert "Первая оплата – 13 255 ₽, можно сейчас." in page.text
    assert "Окно оплаты закончилось" not in page.text
