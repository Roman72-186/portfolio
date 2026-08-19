"""Тесты Telegram Login (OIDC) — основной вход на сайте, Фаза замены
deep-link-флоу на бота (см. app/services/telegram_login.py).

Зеркалит тесты VK OAuth (test_routes_auth.py): та же PKCE-схема (Redis +
cookie fallback), но проверка id_token и обмен кода замоканы напрямую —
детали алгоритма JWT/JWKS покрыты отдельно в тестах telegram_login.py при
необходимости, здесь — контракт роутов.
"""
from urllib.parse import parse_qs, urlparse

import app.api.auth as auth_module


def _enable_telegram_login(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_login_client_id", "123456")
    monkeypatch.setattr(settings, "telegram_login_client_secret", "secret")


def test_telegram_login_disabled_when_not_configured(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_login_client_id", "")
    monkeypatch.setattr(settings, "telegram_login_client_secret", "")

    resp = client.get("/auth/telegram-login", follow_redirects=False)
    assert resp.status_code == 302
    assert "error" in resp.headers["location"]


def test_telegram_login_redirects_when_configured(client, monkeypatch):
    _enable_telegram_login(monkeypatch)

    resp = client.get("/auth/telegram-login", follow_redirects=False)
    assert resp.status_code == 302
    assert "oauth.telegram.org" in resp.headers["location"]


def test_telegram_login_stores_pkce_in_redis(client, monkeypatch):
    _enable_telegram_login(monkeypatch)

    captured = {}

    def fake_set_pkce(state: str, code_verifier: str, ttl: int = 300) -> bool:
        captured["state"] = state
        captured["code_verifier"] = code_verifier
        captured["ttl"] = ttl
        return True

    monkeypatch.setattr(auth_module, "set_telegram_oidc_pkce", fake_set_pkce)

    resp = client.get("/auth/telegram-login", follow_redirects=False)

    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    assert captured["state"] == state
    assert captured["code_verifier"]
    assert captured["ttl"] == 600

    pkce_cookie = resp.cookies.get("tg_pkce_cv")
    assert pkce_cookie
    cookie_data = auth_module._signer.loads(pkce_cookie, max_age=600)
    assert cookie_data["st"] == state


def test_telegram_login_callback_creates_new_user_and_session(client, db, monkeypatch):
    async def fake_exchange_code(code: str, code_verifier: str) -> dict:
        assert code == "good-code"
        assert code_verifier == "redis-verifier"
        return {"id_token": "fake.jwt.token"}

    def fake_verify_id_token(id_token: str) -> dict:
        assert id_token == "fake.jwt.token"
        return {
            "id": 555111222,
            "preferred_username": "new_student",
            "given_name": "Новый",
            "family_name": "Ученик",
        }

    async def fake_check_membership(_chat_id: int) -> bool:
        return True

    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda state: {
        "code_verifier": "redis-verifier",
    } if state == "redis-state" else None)
    monkeypatch.setattr(auth_module, "tg_exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "tg_verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)

    resp = client.get(
        "/auth/telegram-login/callback?code=good-code&state=redis-state",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet"
    assert "session_id" in resp.cookies

    from app.models.user import User
    user = db.query(User).filter(User.telegram_chat_id == 555111222).first()
    assert user is not None
    assert user.tg_username == "new_student"
    assert user.is_group_member is True


def test_telegram_login_callback_matches_existing_user_by_chat_id(client, db, user_factory, monkeypatch):
    existing = user_factory(vk_id=-42, name="Действующий ученик")
    existing.telegram_chat_id = 777888999
    db.commit()

    async def fake_exchange_code(*_a, **_k) -> dict:
        return {"id_token": "fake.jwt.token"}

    def fake_verify_id_token(_id_token: str) -> dict:
        return {"id": 777888999, "preferred_username": "existing_tg", "given_name": "X", "family_name": "Y"}

    async def fake_check_membership(_chat_id: int) -> bool:
        return True

    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda state: {
        "code_verifier": "redis-verifier",
    } if state == "redis-state" else None)
    monkeypatch.setattr(auth_module, "tg_exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "tg_verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)

    resp = client.get(
        "/auth/telegram-login/callback?code=good-code&state=redis-state",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet"

    from app.models.user import User
    matches = db.query(User).filter(User.telegram_chat_id == 777888999).all()
    assert len(matches) == 1
    assert matches[0].id == existing.id


def test_telegram_login_callback_missing_redis_pkce_shows_session_error(client, monkeypatch):
    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda _state: None)

    resp = client.get(
        "/auth/telegram-login/callback?code=good-code&state=missing-state",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "Ошибка сессии" in resp.text or "очистите cookies" in resp.text


def test_telegram_login_callback_denied_when_not_member(client, db, monkeypatch):
    async def fake_exchange_code(*_a, **_k) -> dict:
        return {"id_token": "fake.jwt.token"}

    def fake_verify_id_token(_id_token: str) -> dict:
        return {"id": 111222333, "preferred_username": "denied_user", "given_name": "D", "family_name": "U"}

    async def fake_check_membership(_chat_id: int) -> bool:
        return False

    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda state: {
        "code_verifier": "redis-verifier",
    } if state == "redis-state" else None)
    monkeypatch.setattr(auth_module, "tg_exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "tg_verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)

    resp = client.get(
        "/auth/telegram-login/callback?code=good-code&state=redis-state",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "Нет доступа" in resp.text

    from app.models.user import User
    assert db.query(User).filter(User.telegram_chat_id == 111222333).count() == 0


def test_telegram_login_callback_inconclusive_membership_shows_retry(client, monkeypatch):
    async def fake_exchange_code(*_a, **_k) -> dict:
        return {"id_token": "fake.jwt.token"}

    def fake_verify_id_token(_id_token: str) -> dict:
        return {"id": 444555666, "preferred_username": "u", "given_name": "U", "family_name": "V"}

    async def fake_check_membership(_chat_id: int) -> None:
        return None

    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda state: {
        "code_verifier": "redis-verifier",
    } if state == "redis-state" else None)
    monkeypatch.setattr(auth_module, "tg_exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "tg_verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)

    resp = client.get(
        "/auth/telegram-login/callback?code=good-code&state=redis-state",
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "минуту" in resp.text
