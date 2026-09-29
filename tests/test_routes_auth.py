"""Tests for authentication routes: /, /auth/link, /auth/vk/login, /logout, SSO."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

import app.api.auth as auth_module
from app.config import settings as _app_settings
from app.dependencies import _as_utc
from app.models.session import Session as DbSession
from app.services.auth_links import issue_one_time_login_link, issue_sso_token


# ---------------------------------------------------------------------------
# GET / — entry point / login page
# ---------------------------------------------------------------------------

def test_session_timestamp_normalizer_accepts_sqlite_naive_datetime():
    value = datetime(2026, 7, 13, 10, 0, 0)
    normalized = _as_utc(value)
    assert normalized.tzinfo is timezone.utc
    assert normalized.hour == 10


def test_root_no_session_shows_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert "вход" in resp.text.lower() or "войти" in resp.text.lower() or "login" in resp.text.lower()


def test_root_with_valid_session_redirects_to_cabinet(client, db, user_factory, session_factory):
    user = user_factory()
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet" in resp.headers["location"]


def test_root_with_expired_session_shows_login(client, db, user_factory, session_factory):
    user = user_factory()
    sess = session_factory(user, hours=-1)  # expired 1 hour ago

    client.cookies.set("session_id", sess.id)
    resp = client.get("/", follow_redirects=False)
    # Expired session → stays on login page (no redirect to cabinet)
    assert resp.status_code == 200


def test_root_with_inactive_session_shows_login(client, db, user_factory, session_factory):
    user = user_factory()
    sess = session_factory(user, active=False)

    client.cookies.set("session_id", sess.id)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200


def test_root_error_param_shown(client):
    resp = client.get("/?error=session_expired")
    assert resp.status_code == 200
    assert "Сессия" in resp.text or "истекла" in resp.text


# ---------------------------------------------------------------------------
# GET /auth/vk/login — VK OAuth entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GET /auth/link — one-time magic link login
# ---------------------------------------------------------------------------

def test_auth_link_no_token_shows_error(client):
    resp = client.get("/auth/link", follow_redirects=False)
    assert resp.status_code == 200
    assert "повреждена" in resp.text or "неполная" in resp.text


def test_auth_link_invalid_token_shows_error(client):
    resp = client.get("/auth/link?token=badtoken123", follow_redirects=False)
    assert resp.status_code == 200
    assert "недействительна" in resp.text


def test_auth_link_valid_token_creates_session_and_redirects(client, db, user_factory):
    user = user_factory()
    url, _ = issue_one_time_login_link(db, user=user, base_url="https://testserver")
    token = url.split("token=")[-1]

    resp = client.get(f"/auth/link?token={token}", follow_redirects=False)

    assert resp.status_code == 302
    assert "/cabinet" in resp.headers["location"]
    assert "session_id" in resp.cookies


def test_auth_link_expired_token_shows_error(client, db, user_factory):
    user = user_factory()
    url, issued_token = issue_one_time_login_link(db, user=user, base_url="https://testserver")
    token = url.split("token=")[-1]

    # Manually expire
    issued_token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    resp = client.get(f"/auth/link?token={token}", follow_redirects=False)
    assert resp.status_code == 200
    assert "истекла" in resp.text


def test_auth_link_inactive_user_shows_denied(client, db, user_factory):
    user = user_factory(is_active=False)
    url, _ = issue_one_time_login_link(db, user=user, base_url="https://testserver")
    token = url.split("token=")[-1]

    resp = client.get(f"/auth/link?token={token}", follow_redirects=False)
    assert resp.status_code == 200
    assert "отключен" in resp.text or "заблокирован" in resp.text.lower() or "denied" in resp.url.lower() or "доступ" in resp.text.lower()


def test_auth_link_non_member_shows_denied(client, db, user_factory):
    user = user_factory(is_group_member=False)
    url, _ = issue_one_time_login_link(db, user=user, base_url="https://testserver")
    token = url.split("token=")[-1]

    resp = client.get(f"/auth/link?token={token}", follow_redirects=False)
    assert resp.status_code == 200
    # Should show denied page (not cabinet)
    assert "/cabinet" not in str(resp.url)


# ---------------------------------------------------------------------------
# GET /auth/handoff — fresh login link for in-app→external browser handoff
# ---------------------------------------------------------------------------

def test_auth_handoff_requires_auth(client):
    # Same Accept header the base.html escape script sends → JSON 401 (no redirect).
    resp = client.get("/auth/handoff", headers={"Accept": "application/json"})
    assert resp.status_code == 401


def test_auth_handoff_issues_working_fresh_login_link(client, db, user_factory):
    user = user_factory()
    url, _ = issue_one_time_login_link(db, user=user, base_url="https://testserver")
    token = url.split("token=")[-1]

    # Log in inside the "in-app browser" (sets session cookie on client)
    login = client.get(f"/auth/link?token={token}", follow_redirects=False)
    assert "session_id" in login.cookies

    resp = client.get("/auth/handoff")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "/auth/link?token=" in body["login_url"]

    # The fresh token logs a cookie-less ("external") browser in.
    fresh_token = body["login_url"].split("token=")[-1]
    client.cookies.clear()
    fresh = client.get(f"/auth/link?token={fresh_token}", follow_redirects=False)
    assert fresh.status_code == 302
    assert "/cabinet" in fresh.headers["location"]


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------

def test_logout_invalidates_session(client, db, user_factory, session_factory):
    user = user_factory()
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.post("/logout", follow_redirects=False)

    assert resp.status_code == 302
    # Session must be marked inactive in DB
    db.refresh(sess)
    assert sess.is_active is False


def test_logout_redirects_to_login(client, db, user_factory, session_factory):
    user = user_factory()
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.post("/logout", follow_redirects=False)
    assert resp.headers["location"] == "/login"


def test_logout_without_session_still_redirects(client):
    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /cabinet/3dlab/enter — SSO redirect to 3D Lab
# ---------------------------------------------------------------------------

def test_3dlab_enter_requires_auth(client):
    resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 302


def test_3dlab_enter_lab3d_not_configured_returns_503(admin_client):
    client, _ = admin_client
    with patch.object(_app_settings, "lab3d_url", ""):
        resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 503


def test_3dlab_enter_student_redirected_to_learning_while_lab_closed(auth_client, db):
    """Лаборатория закрыта ученикам на сентябрь 2026 (`LAB3D_OPEN_FOR_STUDENTS`):
    SSO-токен ученику не выдаётся."""
    client, _ = auth_client
    with patch.object(_app_settings, "lab3d_url", "https://3dlab.example.com"), \
         patch.object(_app_settings, "sso_token_ttl_minutes", 2):
        resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/learning"


def test_3dlab_enter_admin_group_member_redirects_with_token(admin_client):
    client, _ = admin_client
    with patch.object(_app_settings, "lab3d_url", "https://3dlab.example.com"), \
         patch.object(_app_settings, "sso_token_ttl_minutes", 2):
        resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "3dlab.example.com/auth/sso" in location
    assert "token=" in location


def test_embedded_3dlab_closed_for_student(auth_client):
    """Созвон 16.09.2026: на сентябрь лаборатория ученикам закрыта. Прямая
    ссылка уводит в ленту, а не на `/denied` про «закрытое сообщество»."""
    client, _ = auth_client
    resp = client.get("/3dlab", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/learning"


def test_embedded_3dlab_available_for_admin(admin_client):
    client, _ = admin_client
    resp = client.get("/3dlab", follow_redirects=False)
    assert resp.status_code == 200
    assert b"/static/3dlab/js/app.js" in resp.content


def test_embedded_3dlab_available_for_student_outside_vk_group(client, db, user_factory, session_factory, monkeypatch):
    """Since 2026-07-05, an assigned student role grants 3D Lab access —
    когда лаборатория открыта ученикам (`LAB3D_OPEN_FOR_STUDENTS`)."""
    from app.services import navigation
    monkeypatch.setattr(navigation, "LAB3D_OPEN_FOR_STUDENTS", True)
    user = user_factory(is_group_member=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/3dlab", follow_redirects=False)
    assert resp.status_code == 200
    assert b"/static/3dlab/js/app.js" in resp.content


def test_denied_page_exists(client):
    resp = client.get("/denied", follow_redirects=False)
    assert resp.status_code == 403
    assert "404" not in resp.text


def test_3dlab_enter_student_outside_vk_group_redirects_with_token(client, db, user_factory, session_factory, monkeypatch):
    from app.services import navigation
    monkeypatch.setattr(navigation, "LAB3D_OPEN_FOR_STUDENTS", True)
    user = user_factory(is_group_member=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    with patch.object(_app_settings, "lab3d_url", "https://3dlab.example.com"), \
         patch.object(_app_settings, "sso_token_ttl_minutes", 2):
        resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 302
    assert "3dlab.example.com/auth/sso" in resp.headers["location"]
    assert "token=" in resp.headers["location"]


# ---------------------------------------------------------------------------
# POST /auth/internal/sso/verify — 3D Lab token verification
# ---------------------------------------------------------------------------

_LAB_TOKEN = "test-lab-secret-token"


def test_sso_verify_invalid_lab_token_returns_401(client):
    with patch.object(_app_settings, "lab3d_internal_token", _LAB_TOKEN):
        resp = client.post(
            "/auth/internal/sso/verify",
            json={"token": "any"},
            headers={"X-Internal-Token": "wrong-secret"},
        )
    assert resp.status_code == 401


def test_sso_verify_invalid_sso_token_returns_400(client, db, user_factory):
    user_factory()
    with patch.object(_app_settings, "lab3d_internal_token", _LAB_TOKEN):
        resp = client.post(
            "/auth/internal/sso/verify",
            json={"token": "nonexistent-token"},
            headers={"X-Internal-Token": _LAB_TOKEN},
        )
    assert resp.status_code == 400
    assert resp.json()["reason"] == "invalid"


def test_sso_verify_valid_token_returns_user(auth_client, db):
    client, user = auth_client
    raw_token, _ = issue_sso_token(db, user=user, ttl_minutes=2)

    with patch.object(_app_settings, "lab3d_internal_token", _LAB_TOKEN):
        resp = client.post(
            "/auth/internal/sso/verify",
            json={"token": raw_token},
            headers={"X-Internal-Token": _LAB_TOKEN},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["vk_id"] == user.vk_id
    assert data["is_group_member"] is True


def test_sso_verify_token_single_use(auth_client, db):
    """Second call with the same token must return reason=used."""
    client, user = auth_client
    raw_token, _ = issue_sso_token(db, user=user, ttl_minutes=2)

    with patch.object(_app_settings, "lab3d_internal_token", _LAB_TOKEN):
        client.post(
            "/auth/internal/sso/verify",
            json={"token": raw_token},
            headers={"X-Internal-Token": _LAB_TOKEN},
        )
        resp2 = client.post(
            "/auth/internal/sso/verify",
            json={"token": raw_token},
            headers={"X-Internal-Token": _LAB_TOKEN},
        )
    assert resp2.status_code == 400
    assert resp2.json()["reason"] == "used"


def test_sso_verify_expired_token_returns_400(client, db, user_factory):
    from app.models.login_token import LoginToken
    from app.services.auth_links import _hash_token

    user = user_factory()
    raw_token = "expired-raw-token-xyz"
    expired_token = LoginToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        issued_by="3dlab-sso",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(expired_token)
    db.commit()

    with patch.object(_app_settings, "lab3d_internal_token", _LAB_TOKEN):
        resp = client.post(
            "/auth/internal/sso/verify",
            json={"token": raw_token},
            headers={"X-Internal-Token": _LAB_TOKEN},
        )
    assert resp.status_code == 400
    assert resp.json()["reason"] == "expired"


def test_internal_issue_link_is_disabled_with_n8n(client, db):
    before = db.query(auth_module.User).count()

    with patch.object(_app_settings, "n8n_enabled", False), \
         patch.object(_app_settings, "internal_api_token", "test-internal-token"):
        resp = client.post(
            "/auth/internal/issue-link",
            headers={"X-Internal-Token": "test-internal-token"},
            json={
                "vk_id": 990001,
                "name": "Disabled n8n user",
                "tariff": "TEST",
                "is_group_member": True,
            },
        )

    assert resp.status_code == 503
    assert db.query(auth_module.User).count() == before


# ---------------------------------------------------------------------------
# Ручное ФИО переживает вход (25.09.2026)
#
# До этой правки каждый вход переписывал имя данными из ВК/Telegram, и правка
# ФИО в «Личной информации» откатывалась при следующем входе — владелец менял
# по несколько раз, а имя возвращалось.
# ---------------------------------------------------------------------------

def test_link_upsert_keeps_manually_edited_name(db, user_factory):
    """Анкета заполнена — имя из профиля ВК поверх своего не ставится."""
    user = user_factory(vk_id=555_101, name="Старое Имя")
    user.first_name, user.last_name, user.name = "Анна", "Смирнова", "Анна Смирнова"
    db.commit()

    auth_module._upsert_user(
        db, vk_id=555_101, name="Anya Smi", first_name="Anya", last_name="Smi",
        photo_url="https://vk.com/photo.jpg",
    )
    db.commit()
    db.refresh(user)

    assert (user.first_name, user.last_name, user.name) == ("Анна", "Смирнова", "Анна Смирнова")
    assert user.photo_url == "https://vk.com/photo.jpg"  # аватар по-прежнему обновляется


def test_link_upsert_fills_empty_name_from_profile(db, user_factory):
    """Своего ФИО ещё нет — заполняем из ВК, как раньше."""
    user = user_factory(vk_id=555_102, name="Ученик", profile_completed=False)
    user.first_name = user.last_name = None
    db.commit()

    auth_module._upsert_user(db, vk_id=555_102, name="Anya Smi", first_name="Anya", last_name="Smi")
    db.commit()
    db.refresh(user)

    assert (user.first_name, user.last_name, user.name) == ("Anya", "Smi", "Anya Smi")


def test_link_upsert_keeps_staff_name_without_questionnaire(db, user_factory):
    """У сотрудника анкеты нет — ФИО ему заводит суперадмин, ВК его не правит."""
    user = user_factory(vk_id=555_103, name="Куратор Лиза", role_name="куратор", profile_completed=False)
    user.first_name, user.last_name = "Лиза", "Куратор"
    db.commit()

    auth_module._upsert_user(db, vk_id=555_103, name="Liza K", first_name="Liza", last_name="K")
    db.commit()
    db.refresh(user)

    assert (user.first_name, user.last_name) == ("Лиза", "Куратор")


def test_telegram_login_keeps_name_but_syncs_username(db, user_factory):
    """ФИО остаётся своим, а ник Telegram синхронизируется намеренно."""
    user = user_factory(vk_id=555_104, name="Анна Смирнова")
    user.telegram_chat_id = 777_101
    user.first_name, user.last_name, user.tg_username = "Анна", "Смирнова", "anna_s"
    db.commit()

    tg_from = auth_module._TgFrom(id=777_101, first_name="Anya", last_name="S", username="anya_tg")
    auth_module._upsert_telegram_user(db, chat_id=777_101, tg_from=tg_from, is_group_member=True)
    db.commit()
    db.refresh(user)

    assert (user.first_name, user.last_name, user.name) == ("Анна", "Смирнова", "Анна Смирнова")
    assert user.tg_username == "anya_tg"
