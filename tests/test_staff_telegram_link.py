"""Одна ссылка на всех сотрудников — подключить Telegram для уведомлений
(владелец 22.10.2026). Сайт узнаёт сотрудника по входу и уводит его в бота
с личной ссылкой; ученику ссылка не выдаётся. Код — `api/auth.py`,
`STAFF_TELEGRAM_PATH`."""

from unittest.mock import patch

import bcrypt
import pytest

from app.config import settings
from app.models.telegram_link_token import TelegramLinkToken
from app.services.auth_links import consume_telegram_link_token

BOT = "apparchi_test_bot"


@pytest.fixture(autouse=True)
def _bot_username():
    with patch.object(settings, "telegram_bot_username", BOT):
        yield


def _with_password(db, user, login, password="secret42"):
    user.staff_login = login
    user.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db.commit()
    return user


def _token_from(location: str) -> str:
    prefix = f"https://t.me/{BOT}?start="
    assert location.startswith(prefix), location
    return location[len(prefix):]


def test_link_shows_login_form_without_session(client):
    page = client.get("/telegram-staff")
    assert page.status_code == 200
    assert "Подключить Telegram" in page.text
    assert 'action="/telegram-staff"' in page.text


def test_curator_login_leads_to_personal_bot_link(client, db, user_factory):
    """Вход логином и паролем — сразу в бота с личной ссылкой, без сессии
    кабинета; по ссылке бот узнаёт именно этого куратора."""
    curator = _with_password(
        db, user_factory(vk_id=996_001, name="Куратор Телеграм", role_name="куратор"),
        "curator_tg",
    )
    response = client.post(
        "/telegram-staff", data={"login": "curator_tg", "password": "secret42"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "session_id" not in response.cookies
    raw_token = _token_from(response.headers["location"])
    _token, user, error = consume_telegram_link_token(db, raw_token=raw_token)
    assert error is None and user.id == curator.id


def test_logged_in_curator_goes_straight_to_bot(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=996_011, name="Куратор С Сессией", role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)
    response = client.get("/telegram-staff", follow_redirects=False)
    assert response.status_code == 302
    _token_from(response.headers["location"])


def test_student_gets_no_link(client, db, user_factory, session_factory):
    student = _with_password(db, user_factory(vk_id=996_021, name="Ученик"), "student_tg")
    response = client.post(
        "/telegram-staff", data={"login": "student_tg", "password": "secret42"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "только для преподавателей" in response.text
    client.cookies.set("session_id", session_factory(student).id)
    assert client.get("/telegram-staff", follow_redirects=False).status_code == 200
    assert db.query(TelegramLinkToken).filter_by(user_id=student.id).count() == 0


def test_wrong_password_is_refused(client, db, user_factory):
    _with_password(
        db, user_factory(vk_id=996_031, name="Куратор Пароль", role_name="куратор"), "curator_pw",
    )
    response = client.post(
        "/telegram-staff", data={"login": "curator_pw", "password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Неверный логин или пароль" in response.text
