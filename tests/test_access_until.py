"""Срок доступа ученика (`User.access_until`) — пробный набор предобучения.

Владелец 11.09.2026: «доступ закрыт и он остаётся только на экране Личная
информация, ссылка на поддержку». Отсюда три вещи, которые тут проверяются:
закрытое закрыто, «Личная информация» открыта, и никого лишнего срок не задел.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.navigation import student_nav_items
from app.services.tz import msk_input_value, msk_text, parse_msk_local


def _hours_ago(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _hours_ahead(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ── Гейт: что закрыто и что открыто ──────────────────────────────────────────

def test_expired_student_redirected_from_learning(db, client, session_factory, user_factory):
    """Истёк срок — учебный раздел уводит на «Личную информацию»."""
    student = user_factory()
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/learning", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal"


def test_expired_student_can_open_personal(db, client, session_factory, user_factory):
    """«Личная информация» остаётся открытой — иначе человеку некуда прийти
    за оплатой."""
    student = user_factory()
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/personal")

    assert resp.status_code == 200
    assert "Доступ закрыт" in resp.text
    # Меню проверяем на самой странице, а не вызовом `student_nav_items`:
    # признак едет через шаблон с `|default(false)`, и потеряйся он по дороге —
    # ученик получил бы полное меню из ссылок, которые тут же его отбрасывают.
    assert "/cabinet/learning" not in resp.text
    assert "/cabinet/tracker" not in resp.text


def test_expired_student_api_gets_403_json(db, client, session_factory, user_factory):
    """Фоновому запросу отдаём честный 403, а не редирект на HTML-страницу —
    иначе экран молча покажет пустоту вместо причины."""
    student = user_factory()
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/tracker", headers={"accept": "application/json"})

    assert resp.status_code == 403


def test_future_deadline_does_not_block(db, client, session_factory, user_factory):
    """Срок ещё не наступил — доступ обычный."""
    student = user_factory()
    student.access_until = _hours_ahead(24)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    assert client.get("/cabinet/personal").status_code == 200
    assert "Доступ закрыт" not in client.get("/cabinet/personal").text


def test_no_deadline_does_not_block(auth_client):
    """Пустой срок — как у всех действующих учеников, ничего не меняется."""
    client, _student = auth_client

    assert client.get("/cabinet/personal").status_code == 200


def test_expired_deadline_ignored_for_staff(db, client, session_factory, user_factory):
    """Случайная дата на строке сотрудника не должна запирать ему кабинет:
    срок держит только учеников."""
    staff = user_factory(
        vk_id=555_001, name="Куратор", role_name="куратор", is_admin=False,
    )
    staff.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(staff).id)

    resp = client.get("/cabinet/students", follow_redirects=False)

    assert resp.status_code != 302 or resp.headers.get("location") != "/cabinet/personal"


def test_expired_student_without_profile_does_not_loop(db, client, session_factory, user_factory):
    """Незаполненная анкета плюс истёкший срок — самая опасная пара: редирект
    на анкету упирался бы в запрет и отбрасывал обратно, и страница зациклилась
    бы насмерть."""
    student = user_factory(profile_completed=False)
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/personal", follow_redirects=False)

    assert resp.status_code == 200


# ── Навигация ────────────────────────────────────────────────────────────────

def test_nav_keeps_only_personal_when_expired():
    items = student_nav_items(access_expired=True)

    assert [item.key for item in items] == ["personal"]


def test_nav_full_by_default():
    assert len(student_nav_items()) > 1
    assert len(student_nav_items(access_expired=False)) > 1


# ── Время ────────────────────────────────────────────────────────────────────

def test_msk_helpers_round_trip():
    """Что показали в форме, то и приняли назад: `datetime-local` ходит в
    московском времени, в базе лежит UTC."""
    parsed = parse_msk_local("2026-09-27T23:30")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert msk_input_value(parsed) == "2026-09-27T23:30"
    assert msk_text(parsed) == "27.09.2026 в 23:30"


def test_msk_helpers_treat_naive_as_utc():
    """SQLite отдаёт время без таймзоны — считаем его UTC, а не временем
    машины, иначе отсечка съедет на часовой пояс разработчика."""
    naive = datetime(2026, 9, 27, 20, 30)

    assert msk_text(naive) == "27.09.2026 в 23:30"


def test_msk_helpers_on_empty():
    assert msk_input_value(None) == ""
    assert msk_text(None) == ""


# ── Правка срока персоналом ──────────────────────────────────────────────────

@pytest.fixture()
def staff_client(client, session_factory, user_factory):
    staff = user_factory(vk_id=777_001, name="Админ", role_name="админ", is_admin=True)
    client.cookies.set("session_id", session_factory(staff).id)
    return client, staff


def _profile_form(**extra):
    body = {
        "first_name": "Иван",
        "last_name": "Петров",
        "phone": "+79990000000",
    }
    body.update(extra)
    return body


def test_staff_sets_access_until(db, staff_client, user_factory):
    client, _staff = staff_client
    student = user_factory(vk_id=100_777)

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data=_profile_form(access_until="2026-09-27T23:30"),
    )

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert msk_input_value(student.access_until) == "2026-09-27T23:30"


def test_staff_clears_access_until(db, staff_client, user_factory):
    """Пустое поле снимает ограничение — так оплативший возвращается к учёбе."""
    client, _staff = staff_client
    student = user_factory(vk_id=100_778)
    student.access_until = _hours_ago(1)
    db.commit()

    resp = client.post(
        f"/cabinet/students/{student.id}/profile", data=_profile_form(access_until=""),
    )

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert student.access_until is None


def test_staff_gets_error_on_broken_date(db, staff_client, user_factory):
    """Мусор в поле — ошибка, а не молчаливое снятие срока: иначе опечатка
    открывала бы доступ тому, кому его закрывают."""
    client, _staff = staff_client
    student = user_factory(vk_id=100_779)
    student.access_until = _hours_ago(1)
    db.commit()
    before = student.access_until

    resp = client.post(
        f"/cabinet/students/{student.id}/profile", data=_profile_form(access_until="не дата"),
    )

    assert resp.status_code == 400
    db.refresh(student)
    assert student.access_until == before
