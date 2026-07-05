"""Tests for authentication routes: /, /auth/link, /auth/vk/login, /logout, SSO."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

import app.api.auth as auth_module
from app.config import settings as _app_settings
from app.models.session import Session as DbSession
from app.services.auth_links import issue_one_time_login_link, issue_sso_token


# ---------------------------------------------------------------------------
# GET / — entry point / login page
# ---------------------------------------------------------------------------

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

def test_vk_login_disabled_when_not_configured(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "vk_app_id", "")

    resp = client.get("/auth/vk/login", follow_redirects=False)
    assert resp.status_code == 302
    assert "error" in resp.headers["location"]


def test_vk_login_redirects_to_vk_when_configured(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "vk_app_id", "12345")
    monkeypatch.setattr(settings, "vk_app_secret", "secret")
    monkeypatch.setattr(settings, "vk_group_id", 99999)

    resp = client.get("/auth/vk/login", follow_redirects=False)
    assert resp.status_code == 302
    assert "vk.com" in resp.headers["location"] or "id.vk.com" in resp.headers["location"]


def test_vk_login_stores_pkce_in_redis(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vk_app_id", "12345")
    monkeypatch.setattr(settings, "vk_app_secret", "secret")
    monkeypatch.setattr(settings, "vk_group_id", 99999)

    captured = {}

    def fake_set_vk_pkce(state: str, code_verifier: str, ttl: int = 300) -> bool:
        captured["state"] = state
        captured["code_verifier"] = code_verifier
        captured["ttl"] = ttl
        return True

    monkeypatch.setattr(auth_module, "set_vk_pkce", fake_set_vk_pkce)

    resp = client.get("/auth/vk/login", follow_redirects=False)

    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    assert captured["state"] == state
    assert captured["code_verifier"]
    assert captured["ttl"] == 300

    pkce_cookie = resp.cookies.get("pkce_cv")
    assert pkce_cookie
    cookie_data = auth_module._signer.loads(pkce_cookie, max_age=300)
    assert cookie_data["st"] == state
    assert cookie_data["v"] == 2


def test_vk_callback_succeeds_with_redis_pkce(client, monkeypatch):
    async def fake_exchange_code(code: str, code_verifier: str, device_id: str) -> dict:
        assert code == "good-code"
        assert code_verifier == "redis-verifier"
        assert device_id == "device-123"
        return {"access_token": "token-123", "user_id": 658607006}

    async def fake_check_group_membership(*_args, **_kwargs) -> bool:
        return True

    async def fake_get_user_info(*_args, **_kwargs) -> dict:
        return {
            "vk_id": 658607006,
            "name": "Test Student",
            "first_name": "Test",
            "last_name": "Student",
            "photo_url": None,
        }

    async def fake_sync_drive_works(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(auth_module, "pop_vk_pkce", lambda state: {
        "code_verifier": "redis-verifier",
        "created_at": "2026-04-19T00:00:00+00:00",
    } if state == "redis-state" else None)
    monkeypatch.setattr(auth_module, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "check_group_membership", fake_check_group_membership)
    monkeypatch.setattr(auth_module, "get_user_info", fake_get_user_info)
    monkeypatch.setattr(auth_module.drive_service, "sync_drive_works", fake_sync_drive_works)

    resp = client.get(
        "/auth/vk/callback?code=good-code&state=redis-state&device_id=device-123",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet"
    assert "session_id" in resp.cookies


def test_vk_callback_missing_redis_pkce_shows_session_error(client, monkeypatch):
    monkeypatch.setattr(auth_module, "pop_vk_pkce", lambda _state: None)

    resp = client.get(
        "/auth/vk/callback?code=good-code&state=missing-state&device_id=device-123",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "Ошибка сессии" in resp.text or "очистите cookies" in resp.text


def test_vk_callback_rejects_replay_after_redis_pkce_is_consumed(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vk_app_id", "12345")
    monkeypatch.setattr(settings, "vk_app_secret", "secret")
    monkeypatch.setattr(settings, "vk_group_id", 99999)
    monkeypatch.setattr(auth_module, "set_vk_pkce", lambda *_args, **_kwargs: True)

    async def fake_exchange_code(_code: str, code_verifier: str, _device_id: str) -> dict:
        assert code_verifier == "redis-verifier"
        return {"access_token": "token-123", "user_id": 658607006}

    async def fake_check_group_membership(*_args, **_kwargs) -> bool:
        return True

    async def fake_get_user_info(*_args, **_kwargs) -> dict:
        return {
            "vk_id": 658607006,
            "name": "Replay User",
            "first_name": "Replay",
            "last_name": "User",
            "photo_url": None,
        }

    async def fake_sync_drive_works(*_args, **_kwargs) -> None:
        return None

    pop_results = iter([
        {"code_verifier": "redis-verifier", "created_at": "2026-04-19T00:00:00+00:00"},
        None,
    ])

    monkeypatch.setattr(auth_module, "pop_vk_pkce", lambda _state: next(pop_results))
    monkeypatch.setattr(auth_module, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "check_group_membership", fake_check_group_membership)
    monkeypatch.setattr(auth_module, "get_user_info", fake_get_user_info)
    monkeypatch.setattr(auth_module.drive_service, "sync_drive_works", fake_sync_drive_works)

    login_resp = client.get("/auth/vk/login", follow_redirects=False)
    state = parse_qs(urlparse(login_resp.headers["location"]).query)["state"][0]

    first = client.get(
        f"/auth/vk/callback?code=good-code&state={state}&device_id=device-123",
        follow_redirects=False,
    )
    second = client.get(
        f"/auth/vk/callback?code=good-code&state={state}&device_id=device-123",
        follow_redirects=False,
    )

    assert first.status_code == 302
    assert second.status_code == 200
    assert "Ошибка сессии" in second.text or "очистите cookies" in second.text


def test_vk_callback_legacy_cookie_fallback_still_works(client, monkeypatch):
    legacy_cookie = auth_module._signer.dumps({
        "cv": "legacy-verifier",
        "st": "legacy-state",
    })
    client.cookies.set("pkce_cv", legacy_cookie)

    async def fake_exchange_code(_code: str, code_verifier: str, _device_id: str) -> dict:
        assert code_verifier == "legacy-verifier"
        return {"access_token": "token-legacy", "user_id": 658607006}

    async def fake_check_group_membership(*_args, **_kwargs) -> bool:
        return True

    async def fake_get_user_info(*_args, **_kwargs) -> dict:
        return {
            "vk_id": 658607006,
            "name": "Legacy User",
            "first_name": "Legacy",
            "last_name": "User",
            "photo_url": None,
        }

    async def fake_sync_drive_works(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(auth_module, "pop_vk_pkce", lambda _state: None)
    monkeypatch.setattr(auth_module, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "check_group_membership", fake_check_group_membership)
    monkeypatch.setattr(auth_module, "get_user_info", fake_get_user_info)
    monkeypatch.setattr(auth_module.drive_service, "sync_drive_works", fake_sync_drive_works)

    resp = client.get(
        "/auth/vk/callback?code=legacy-code&state=legacy-state&device_id=device-123",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet"
    assert "session_id" in resp.cookies


def test_vk_callback_inconclusive_membership_shows_retry_message(client, db, user_factory, monkeypatch):
    """check_group_membership returning None (timeout/VK error) must not be
    treated as a confirmed non-membership: existing user's is_group_member
    stays unchanged and the user sees a retry message, not "Доступ запрещён"."""
    existing = user_factory(vk_id=658607006, is_group_member=True)
    old_check_at = existing.last_vk_check_at

    async def fake_exchange_code(*_args, **_kwargs) -> dict:
        return {"access_token": "token-123", "user_id": 658607006}

    async def fake_check_group_membership(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(auth_module, "pop_vk_pkce", lambda state: {
        "code_verifier": "redis-verifier",
        "created_at": "2026-04-19T00:00:00+00:00",
    } if state == "redis-state" else None)
    monkeypatch.setattr(auth_module, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "check_group_membership", fake_check_group_membership)
    monkeypatch.setattr(_app_settings, "vk_community_token", "")

    resp = client.get(
        "/auth/vk/callback?code=good-code&state=redis-state&device_id=device-123",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "session_id" not in resp.cookies
    assert "Не удалось проверить" in resp.text

    db.refresh(existing)
    assert existing.is_group_member is True
    assert existing.last_vk_check_at == old_check_at


def test_vk_callback_confirmed_non_member_shows_denied(client, db, user_factory, monkeypatch):
    """check_group_membership returning False (VK confirmed: not a member)
    keeps the existing hard-denial behavior and persists is_group_member=False."""
    existing = user_factory(vk_id=658607006, is_group_member=True)

    async def fake_exchange_code(*_args, **_kwargs) -> dict:
        return {"access_token": "token-123", "user_id": 658607006}

    async def fake_check_group_membership(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(auth_module, "pop_vk_pkce", lambda state: {
        "code_verifier": "redis-verifier",
        "created_at": "2026-04-19T00:00:00+00:00",
    } if state == "redis-state" else None)
    monkeypatch.setattr(auth_module, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "check_group_membership", fake_check_group_membership)
    monkeypatch.setattr(_app_settings, "vk_community_token", "")

    resp = client.get(
        "/auth/vk/callback?code=good-code&state=redis-state&device_id=device-123",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "session_id" not in resp.cookies
    assert "Доступ запрещён" in resp.text

    db.refresh(existing)
    assert existing.is_group_member is False
    assert existing.last_vk_check_at is not None


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


def test_3dlab_enter_lab3d_not_configured_returns_503(auth_client):
    client, _ = auth_client
    with patch.object(_app_settings, "lab3d_url", ""):
        resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 503


def test_3dlab_enter_redirects_with_token(auth_client, db):
    client, _ = auth_client
    with patch.object(_app_settings, "lab3d_url", "https://3dlab.example.com"), \
         patch.object(_app_settings, "sso_token_ttl_minutes", 2):
        resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "3dlab.example.com/auth/sso" in location
    assert "token=" in location


def test_3dlab_enter_admin_group_member_redirects_with_token(admin_client):
    client, _ = admin_client
    with patch.object(_app_settings, "lab3d_url", "https://3dlab.example.com"), \
         patch.object(_app_settings, "sso_token_ttl_minutes", 2):
        resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "3dlab.example.com/auth/sso" in location
    assert "token=" in location


def test_embedded_3dlab_available_for_student(auth_client):
    client, _ = auth_client
    resp = client.get("/3dlab", follow_redirects=False)
    assert resp.status_code == 200
    assert b"/static/3dlab/js/app.js" in resp.content


def test_embedded_3dlab_available_for_admin(admin_client):
    client, _ = admin_client
    resp = client.get("/3dlab", follow_redirects=False)
    assert resp.status_code == 200
    assert b"/static/3dlab/js/app.js" in resp.content


def test_embedded_3dlab_not_group_member_redirects_denied(client, db, user_factory, session_factory):
    user = user_factory(is_group_member=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/3dlab", follow_redirects=False)
    assert resp.status_code == 302
    assert "denied" in resp.headers["location"]


def test_embedded_3dlab_not_group_member_denied_page_not_404(client, db, user_factory, session_factory):
    user = user_factory(is_group_member=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/3dlab", follow_redirects=True)
    assert resp.status_code == 403
    assert resp.url.path == "/denied"
    assert "404" not in resp.text


def test_denied_page_exists(client):
    resp = client.get("/denied", follow_redirects=False)
    assert resp.status_code == 403
    assert "404" not in resp.text


def test_3dlab_enter_not_group_member_redirects_denied(client, db, user_factory, session_factory):
    user = user_factory(is_group_member=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/3dlab/enter", follow_redirects=False)
    assert resp.status_code in (302, 403)
    if resp.status_code == 302:
        assert "denied" in resp.headers["location"]


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
