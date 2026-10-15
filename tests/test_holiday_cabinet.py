"""Каникулярный набор, кабинет: короткая анкета с классом, урезанный кабинет,
отдельный список и адреса входа.

Созвон 13.10.2026, план `plans/2026-10-14-apparchi-созвон-13-10.md`, часть 3.3.
Вход по ссылке группы — `tests/test_holiday_intake.py`, разделение программ —
`tests/test_holiday_program.py`.
"""
from datetime import datetime, timedelta, timezone

from app.constants import PROGRAM_HOLIDAY
from app.models.session import Session
from app.models.tag import Tag, UserTag
from app.services.login_ips import device_label, distinct_ips_by_user
from app.services.navigation import student_nav_items
from app.services.report_scope import reportable_student_id_set
from app.services.review_aggregate import _accessible_students
from app.services.tags import ensure_grade_tag


def _holiday(db, user_factory, *, vk_id=300_001, name="Каникулярная Аня", profile_completed=True):
    student = user_factory(vk_id=vk_id, name=name, tariff="", profile_completed=profile_completed)
    student.program = PROGRAM_HOLIDAY
    db.commit()
    return student


def _login_as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def _grade_tags(db, user):
    return sorted(
        name for (name,) in db.query(Tag.name).join(UserTag, UserTag.tag_id == Tag.id)
        .filter(UserTag.user_id == user.id, Tag.name.like("% класс"))
    )


# ---------------------------------------------------------------------------
# Анкета
# ---------------------------------------------------------------------------

def test_holiday_profile_form_is_short_with_class(client, db, user_factory, session_factory):
    student = _holiday(db, user_factory, profile_completed=False)
    page = _login_as(client, session_factory, student).get("/cabinet/profile")

    assert page.status_code == 200
    assert 'name="school_grade"' in page.text
    for field in ("parent_phone", "parent_name", "sdek_address", "email", "birth_date",
                  "university_year", "tariff", "tg_username"):
        assert f'name="{field}"' not in page.text, field
    for field in ("first_name", "last_name", "phone", "city", "timezone"):
        assert f'name="{field}"' in page.text, field


def test_holiday_profile_saves_and_tags_the_class(client, db, user_factory, session_factory):
    student = _holiday(db, user_factory, profile_completed=False)
    student.tg_username = "kanikuly_anya"
    db.commit()

    resp = _login_as(client, session_factory, student).post("/cabinet/profile", data={
        "csrf_token": "x", "first_name": "Аня", "last_name": "Котова", "phone": "8 999 123 45 67",
        "city": "Казань", "timezone": "0", "school_grade": "11",
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/learning?welcome=1"
    db.refresh(student)
    assert student.profile_completed is True
    assert (student.first_name, student.last_name, student.city) == ("Аня", "Котова", "Казань")
    assert student.phone == "+79991234567"
    assert student.school_grade == 11
    assert student.tg_username == "kanikuly_anya"
    assert student.tariff == ""
    assert _grade_tags(db, student) == ["11 класс"]


def test_holiday_profile_requires_class(client, db, user_factory, session_factory):
    student = _holiday(db, user_factory, profile_completed=False)

    resp = _login_as(client, session_factory, student).post("/cabinet/profile", data={
        "csrf_token": "x", "first_name": "Аня", "last_name": "Котова", "phone": "+79991234567",
        "city": "Казань", "timezone": "0", "school_grade": "9",
    })

    assert resp.status_code == 200
    assert "Выбери класс" in resp.text
    db.refresh(student)
    assert student.profile_completed is False


def test_annual_profile_without_parent_phone_explains_instead_of_422(client, db, user_factory, session_factory):
    """`parent_phone` стал необязательным полем формы ради короткой анкеты —
    годовая анкета без него по-прежнему не сохраняется."""
    student = user_factory(vk_id=300_002, profile_completed=False)

    resp = _login_as(client, session_factory, student).post("/cabinet/profile", data={
        "csrf_token": "x", "first_name": "Иван", "last_name": "Петров", "phone": "+79991234567",
    })

    assert resp.status_code == 200
    assert "Введи номер телефона родителя" in resp.text
    db.refresh(student)
    assert student.profile_completed is False


def test_grade_tag_follows_the_class(db, user_factory):
    student = _holiday(db, user_factory)
    student.school_grade = 10
    ensure_grade_tag(db, student)
    student.school_grade = 11
    ensure_grade_tag(db, student)
    db.commit()

    assert _grade_tags(db, student) == ["11 класс"]


# ---------------------------------------------------------------------------
# «Контакты» в «Личной информации» (15.10.2026: требовали все годовые поля)
# ---------------------------------------------------------------------------

def test_holiday_contacts_form_has_the_short_fields(client, db, user_factory, session_factory):
    student = _holiday(db, user_factory)
    student.school_grade = 10
    db.commit()

    page = _login_as(client, session_factory, student).get("/cabinet/personal/contacts")

    assert page.status_code == 200
    for field in ("first_name", "last_name", "city", "timezone", "phone", "school_grade"):
        assert f'name="{field}"' in page.text, field
    for field in ("parent_phone", "parent_name", "sdek_address", "email", "birth_date",
                  "university_year", "tg_username"):
        assert f'name="{field}"' not in page.text, field
    assert '<option value="10" selected>10 класс</option>' in page.text


def test_holiday_contacts_save_without_annual_fields(client, db, user_factory, session_factory):
    student = _holiday(db, user_factory)
    student.school_grade = 10
    student.tg_username = "kanikuly_anya"
    enrolled = (student.enrollment_year, student.enrolled_at)
    db.commit()
    ensure_grade_tag(db, student)
    db.commit()

    resp = _login_as(client, session_factory, student).post("/cabinet/personal/contacts", data={
        "csrf_token": "x", "first_name": "Аня", "last_name": "Котова", "city": "Самара",
        "timezone": "1", "phone": "8 999 765 43 21", "school_grade": "11",
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal?saved=1"
    db.refresh(student)
    assert (student.first_name, student.last_name, student.city, student.timezone) == (
        "Аня", "Котова", "Самара", "1",
    )
    assert student.phone == "+79997654321"
    assert student.school_grade == 11
    assert student.tg_username == "kanikuly_anya"
    assert (student.enrollment_year, student.enrolled_at) == enrolled
    assert _grade_tags(db, student) == ["11 класс"]


def test_holiday_contacts_keep_the_short_rules(client, db, user_factory, session_factory):
    student = _holiday(db, user_factory)

    resp = _login_as(client, session_factory, student).post("/cabinet/personal/contacts", data={
        "csrf_token": "x", "first_name": "Аня", "last_name": "Котова", "city": "Самара",
        "timezone": "1", "phone": "+79997654321",
    })

    assert resp.status_code == 200
    assert "Выбери класс" in resp.text
    assert "телефона родителя" not in resp.text
    db.refresh(student)
    assert student.city != "Самара"


def test_nightly_nick_check_skips_holiday(db, user_factory, monkeypatch):
    """Ник каникулярного ни к чему не привязан, а поля ника в его «Контактах»
    нет: отметка расхождения закрыла бы кабинет без выхода."""
    from unittest.mock import AsyncMock

    from app.services.exam_scheduler import _run_tg_username_check

    student = _holiday(db, user_factory)
    student.telegram_chat_id = 900_301
    student.tg_username = "anya"
    db.commit()
    monkeypatch.setattr(
        "app.services.exam_scheduler.telegram_service.get_chat_username",
        AsyncMock(return_value=(True, "anya_new")),
    )

    _run_tg_username_check()

    db.refresh(student)
    assert student.tg_username_mismatch is False


# ---------------------------------------------------------------------------
# Урезанный кабинет
# ---------------------------------------------------------------------------

def test_limited_menu_keeps_learning_portfolio_personal():
    assert [item.key for item in student_nav_items(limited=True)] == ["learning", "portfolio", "personal"]
    # Истёкший срок строже урезанного кабинета.
    assert [item.key for item in student_nav_items(True, True)] == ["personal"]


def test_holiday_student_sees_only_three_sections(client, db, user_factory, session_factory):
    client = _login_as(client, session_factory, _holiday(db, user_factory))

    for path in ("/cabinet/learning", "/cabinet/portfolio", "/cabinet/personal"):
        assert client.get(path, follow_redirects=False).status_code == 200, path

    for path in ("/cabinet/tracker", "/cabinet/learning/archive", "/cabinet/cycle",
                 "/cabinet/feedback/", "/cabinet/videos", "/cabinet/student"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, path
        assert resp.headers["location"] == "/cabinet/learning", path

    page = client.get("/cabinet/learning")
    assert 'data-nav-key="tracker"' not in page.text
    assert 'data-nav-key="portfolio"' in page.text


def test_limited_refusal_is_json_for_fetch(client, db, user_factory, session_factory):
    client = _login_as(client, session_factory, _holiday(db, user_factory))
    resp = client.get("/cabinet/tracker", headers={"Accept": "application/json"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Раздел закрыт в каникулярном наборе"


def test_annual_student_keeps_full_cabinet(auth_client):
    client, _student = auth_client
    assert client.get("/cabinet/tracker", follow_redirects=False).status_code == 200


# ---------------------------------------------------------------------------
# Отдельный список
# ---------------------------------------------------------------------------

def test_holiday_student_is_out_of_students_and_in_its_own_list(admin_client, db, user_factory):
    client, _admin = admin_client
    holiday = _holiday(db, user_factory, name="Каникулярная Аня")
    user_factory(vk_id=300_003, name="Годовой Петя")
    db.add(Session(user_id=holiday.id, ip="10.0.0.1", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.add(Session(user_id=holiday.id, ip="10.0.0.2", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.commit()

    main = client.get("/cabinet/students")
    assert "Каникулярная Аня" not in main.text
    assert "Годовой Петя" in main.text
    assert "program=holiday" in main.text

    own = client.get("/cabinet/students?program=holiday")
    assert "Каникулярная Аня" in own.text
    assert "Годовой Петя" not in own.text
    assert "IP: 2" in own.text
    assert "Каникулярный набор" in own.text


def test_holiday_student_is_out_of_statistics_point_a_and_review(db, user_factory, admin_user):
    holiday = _holiday(db, user_factory)
    annual = user_factory(vk_id=300_004)

    in_scope = reportable_student_id_set(db)
    assert annual.id in in_scope and holiday.id not in in_scope

    staff = {"role_rank": 5, "user_id": admin_user.id}
    ids = {s.id for s in _accessible_students(db, staff)}
    assert annual.id in ids and holiday.id not in ids


# ---------------------------------------------------------------------------
# Адреса входа
# ---------------------------------------------------------------------------

def test_distinct_ips_count_real_logins_of_the_week_only(db, user_factory, admin_user):
    student = _holiday(db, user_factory)
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    old = datetime.now(timezone.utc) - timedelta(days=8)
    db.add_all([
        Session(user_id=student.id, ip="10.0.0.1", expires_at=later),
        Session(user_id=student.id, ip="10.0.0.1", expires_at=later),
        Session(user_id=student.id, ip="10.0.0.2", expires_at=later),
        Session(user_id=student.id, ip="10.0.0.3", expires_at=later, created_at=old),
        Session(user_id=student.id, ip="10.0.0.4", expires_at=later, impersonated_by_id=admin_user.id),
    ])
    db.commit()

    assert distinct_ips_by_user(db, [student.id]) == {student.id: 2}


def test_device_label_reads_common_browsers():
    assert device_label("Mozilla/5.0 (Linux; Android 14; Pixel 8)") == "Android"
    assert device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)") == "iPhone"
    assert device_label(None) == "Неизвестное устройство"


def test_recent_logins_in_card_for_chief_teacher_only(admin_client, client, db, user_factory, session_factory):
    staff_client, _admin = admin_client
    student = _holiday(db, user_factory)
    db.add(Session(user_id=student.id, ip="10.0.0.9", user_agent="Mozilla/5.0 (iPhone)",
                   expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.commit()

    data = staff_client.get(f"/cabinet/students/{student.id}/statistics").json()
    assert data["recent_logins"][0]["ip"] == "10.0.0.9"
    assert data["recent_logins"][0]["device"] == "iPhone"

    curator = user_factory(vk_id=300_005, role_name="куратор")
    student.curator_id = curator.id
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)
    assert client.get(f"/cabinet/students/{student.id}/statistics").json()["recent_logins"] is None
