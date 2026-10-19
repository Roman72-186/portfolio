"""Статистика и оплаты каникулярного набора: переключатель «Годовой курс /
Каникулярный курс» (владелец 19.10.2026).

План — `plans/2026-10-19-apparchi-статистика-каникулярного.md`. Программу учёта
держит `report_scope`: по умолчанию годовой курс, страница статистики и экран
«Оплаты» на время запроса включают каникулярный.
"""
from datetime import datetime, timedelta, timezone

from app.constants import PROGRAM_ANNUAL, PROGRAM_HOLIDAY
from app.models.payment import STATUS_PAID, STATUS_REFUNDED, Payment
from app.models.tag import Tag, UserTag
from app.services import intake_link as intake_link_service
from app.services import payments as ps
from app.services.report_scope import (
    current_report_program,
    report_program,
    reportable_student_id_set,
)
from app.services.tags import get_or_create_tag


def _track(db, title="Попробуй обучение", price_kop=345500):
    tag = get_or_create_tag(db, title)
    link = intake_link_service.create_holiday_link(
        db, title=title, tag_id=tag.id,
        access_until=datetime.now(timezone.utc) + timedelta(days=10), created_by_id=None,
    )
    link.price_kop = price_kop
    link.is_active = True
    db.commit()
    return link


def _holiday(db, user_factory, *, vk_id, name, profile_completed=True):
    student = user_factory(vk_id=vk_id, name=name, tariff="", profile_completed=profile_completed)
    student.program = PROGRAM_HOLIDAY
    db.commit()
    return student


def _paid(db, student, track, *, now=None):
    """Оплата трека тем же путём, что вебхук: `record_paid`."""
    now = now or datetime.now(timezone.utc)
    payment = Payment(user_id=student.id, period=now.date().replace(day=1), kind="holiday",
                      intake_link_id=track.id, amount_kop=track.price_kop, tariff="", status="pending")
    db.add(payment)
    db.flush()
    ps.record_paid(db, payment, now=now, source="prodamus", paid_sum_kop=track.price_kop,
                   performed_by_id=student.id, prodamus_order_id=f"o-{payment.id}")
    db.commit()
    return payment


def _tags(db, user):
    return {n for (n,) in db.query(Tag.name).join(UserTag, UserTag.tag_id == Tag.id)
            .filter(UserTag.user_id == user.id)}


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


# ── Правило учёта ────────────────────────────────────────────────────────────

def test_report_program_switches_who_is_counted(db, user_factory):
    annual = user_factory(vk_id=320_001)
    holiday = _holiday(db, user_factory, vk_id=320_002, name="Каникулярная Аня")
    # Набор закончился — каникулярный в учёте своей программы всё равно остаётся.
    holiday.access_until = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()

    default = reportable_student_id_set(db)
    assert annual.id in default and holiday.id not in default
    with report_program(PROGRAM_HOLIDAY):
        assert current_report_program() == PROGRAM_HOLIDAY
        assert reportable_student_id_set(db) == {holiday.id}
    assert current_report_program() == PROGRAM_ANNUAL
    with report_program("чужое"):
        assert annual.id in reportable_student_id_set(db)


# ── Статистика активности ────────────────────────────────────────────────────

def test_activity_page_holiday_mode(client, db, user_factory, session_factory, admin_user):
    track = _track(db)
    payer = _holiday(db, user_factory, vk_id=320_011, name="Оплатила Трек")
    payer.tg_username = "payer_nick"
    payer.school_grade = "grade10"
    _holiday(db, user_factory, vk_id=320_012, name="Вошёл Без Оплаты", profile_completed=False)
    annual = user_factory(vk_id=320_013, name="Годовой Петя")
    _paid(db, payer, track)
    client = _login(client, session_factory, admin_user)

    holiday_page = client.get("/cabinet/superadmin/activity?program=holiday")
    assert holiday_page.status_code == 200
    text = holiday_page.text
    assert "Каникулярный набор поимённо" in text
    assert "Оплатила Трек" in text and "Вошёл Без Оплаты" in text
    assert "@payer_nick" in text and "10 класс" in text
    assert "Попробуй обучение" in text and "оплачен" in text and "не оплатил" in text
    assert "Годовой Петя" not in text
    assert "Регистрации по тарифам" not in text
    assert 'href="/cabinet/superadmin/payments?program=holiday"' in text

    annual_page = client.get("/cabinet/superadmin/activity").text
    assert "Каникулярный набор поимённо" not in annual_page
    assert "Годовой Петя" in annual_page
    # Журнал изменений общий (там есть «Оплата #… за трек»), а в учёте её нет.
    assert 'data-student-name="Оплатила Трек"' not in annual_page
    assert "Регистрации по тарифам" in annual_page
    assert 'href="/cabinet/superadmin/activity?program=holiday"' in annual_page
    # Программа учёта сброшена — следующий запрос снова годовой.
    assert annual.id in reportable_student_id_set(db)


# ── Экран «Оплаты» ───────────────────────────────────────────────────────────

def test_payments_holiday_mode_lists_tracks_and_refund(client, db, user_factory, session_factory, admin_user):
    track = _track(db)
    payer = _holiday(db, user_factory, vk_id=320_021, name="Оплатил Каникулы")
    _holiday(db, user_factory, vk_id=320_022, name="Не Оплатил Каникулы")
    payment = _paid(db, payer, track)
    client = _login(client, session_factory, admin_user)

    page = client.get("/cabinet/superadmin/payments?program=holiday")
    assert page.status_code == 200
    assert "Оплатил Каникулы" in page.text and "Попробуй обучение" in page.text
    assert "3 455" in page.text
    assert "Вошли, но не оплатили" in page.text and "Не Оплатил Каникулы" in page.text
    assert f'data-payment="{payment.id}"' in page.text and 'data-kind="holiday"' in page.text

    resp = client.post(f"/cabinet/superadmin/payments/{payment.id}/refund",
                       data={"csrf_token": "x"}, headers={"Accept": "application/json"})
    assert resp.status_code == 200
    db.refresh(payment)
    assert payment.status == STATUS_REFUNDED
    assert "Попробуй обучение" not in _tags(db, payer)


def test_monthly_board_skips_holiday_students(client, db, user_factory, session_factory, admin_user):
    _holiday(db, user_factory, vk_id=320_031, name="Каникулярный Без Окна")
    client = _login(client, session_factory, admin_user)
    page = client.get("/cabinet/superadmin/payments")
    assert page.status_code == 200
    assert "Каникулярный Без Окна" not in page.text
    assert 'href="/cabinet/superadmin/payments?program=holiday"' in page.text
