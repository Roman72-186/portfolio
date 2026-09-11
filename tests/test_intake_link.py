"""Ссылка пробного набора предобучения: `GET /proba` + переключатель на
`/cabinet/periods` (`app/api/auth.py`, `app/api/cabinet_superadmin.py`,
`app/services/intake_link.py`).

Не смешивать с `tests/test_access_until.py` — тот про сам запрет по истёкшему
сроку, этот про вход, который срок проставляет.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import app.api.auth as auth_module
from app.constants import INTAKE_TRIAL_SLUG
from app.models.intake_link import IntakeLink
from app.models.user import User
from app.services.tz import msk_input_value

CHAT_ID = 700_100_200
DEADLINE = datetime(2026, 9, 27, 20, 30, tzinfo=timezone.utc)  # 27.09 23:30 МСК


def _enable_telegram_login(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_login_client_id", "123456")
    monkeypatch.setattr(settings, "telegram_login_client_secret", "secret")


def _make_link(db, *, is_active: bool, access_until=DEADLINE) -> IntakeLink:
    link = IntakeLink(
        slug=INTAKE_TRIAL_SLUG, title="Пробный набор",
        is_active=is_active, access_until=access_until,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def _mock_callback(monkeypatch, *, chat_id: int, intake_slug: str | None, state: str = "redis-state"):
    """Мокает redis PKCE + обмен кода + членство — общий хвост для callback-тестов."""
    extra = {"purpose": auth_module.TG_PURPOSE_LOGIN}
    if intake_slug:
        extra["intake_slug"] = intake_slug

    async def fake_exchange_code(*_a, **_k) -> dict:
        return {"id_token": "fake.jwt.token"}

    def fake_verify_id_token(_id_token: str) -> dict:
        return {"id": chat_id, "preferred_username": "u", "given_name": "И", "family_name": "П"}

    async def fake_check_membership(_chat_id: int) -> bool:
        return True

    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda s: (
        {"code_verifier": "v", **extra} if s == state else None
    ))
    monkeypatch.setattr(auth_module, "tg_exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "tg_verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)


# ---------------------------------------------------------------------------
# GET /proba
# ---------------------------------------------------------------------------

def test_proba_closed_link_shows_closed_screen_without_login_link(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    _make_link(db, is_active=False)

    resp = client.get("/proba", follow_redirects=False)

    assert resp.status_code == 200
    assert "/auth/telegram-login" not in resp.text
    assert "Набор закрыт" in resp.text


def test_proba_no_link_row_shows_closed_screen(client, monkeypatch):
    _enable_telegram_login(monkeypatch)
    resp = client.get("/proba", follow_redirects=False)
    assert resp.status_code == 200
    assert "/auth/telegram-login" not in resp.text


def test_proba_open_link_redirects_to_telegram(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    _make_link(db, is_active=True)

    resp = client.get("/proba", follow_redirects=False)

    assert resp.status_code == 302
    assert "oauth.telegram.org" in resp.headers["location"]


def test_proba_carries_intake_slug_in_redis_and_cookie(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    _make_link(db, is_active=True)

    captured = {}

    def fake_set_pkce(state, code_verifier, ttl=300, extra=None):
        captured["extra"] = extra or {}
        return True

    monkeypatch.setattr(auth_module, "set_telegram_oidc_pkce", fake_set_pkce)

    resp = client.get("/proba", follow_redirects=False)

    assert captured["extra"]["intake_slug"] == INTAKE_TRIAL_SLUG

    pkce_cookie = resp.cookies.get("tg_pkce_cv")
    cookie_data = auth_module._signer.loads(pkce_cookie, max_age=600)
    assert cookie_data["intake_slug"] == INTAKE_TRIAL_SLUG


def test_proba_redis_miss_still_sets_deadline_via_cookie_fallback(client, db, monkeypatch):
    """Промах Redis: cookie собирает настоящий `_signer` через реальный
    GET /proba, а не руками в тесте — иначе подпись/срок жизни могут разойтись
    с тем, что реально шлёт браузер."""
    _enable_telegram_login(monkeypatch)
    _make_link(db, is_active=True)

    start = client.get("/proba", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    # Redis промахнулся — callback обязан достать intake_slug из cookie.
    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda _s: None)

    async def fake_exchange_code(*_a, **_k) -> dict:
        return {"id_token": "fake.jwt.token"}

    def fake_verify_id_token(_id_token: str) -> dict:
        return {"id": CHAT_ID, "preferred_username": "u", "given_name": "И", "family_name": "П"}

    async def fake_check_membership(_chat_id: int) -> bool:
        return True

    monkeypatch.setattr(auth_module, "tg_exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "tg_verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)

    resp = client.get(f"/auth/telegram-login/callback?code=good&state={state}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet"


def test_proba_live_session_redirects_to_cabinet(auth_client):
    client, _user = auth_client
    resp = client.get("/proba", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet"


# ---------------------------------------------------------------------------
# Callback: простановка срока
# ---------------------------------------------------------------------------

def test_new_user_via_intake_gets_deadline(client, db, monkeypatch):
    _make_link(db, is_active=True)
    _mock_callback(monkeypatch, chat_id=CHAT_ID, intake_slug=INTAKE_TRIAL_SLUG)

    resp = client.get(
        "/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False,
    )
    assert resp.status_code == 302

    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).first()
    assert user is not None
    assert user.access_until == DEADLINE


def test_repeat_login_by_intake_link_does_not_move_deadline(client, db, monkeypatch):
    link = _make_link(db, is_active=True)
    _mock_callback(monkeypatch, chat_id=CHAT_ID, intake_slug=INTAKE_TRIAL_SLUG)

    client.get("/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False)
    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).first()
    first_deadline = user.access_until

    # Владелец подвинул дату в админке — уже вошедшего это не должно тронуть.
    link.access_until = DEADLINE + timedelta(days=3)
    db.commit()

    client.get("/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False)
    db.refresh(user)
    assert user.access_until == first_deadline


def test_existing_student_with_empty_deadline_keeps_it_empty(client, db, monkeypatch, user_factory):
    """Действующий ученик, кликнувший по ссылке набора, не должен получить
    закрытие доступа — срок ставится только по-настоящему новым."""
    _make_link(db, is_active=True)
    user = user_factory(vk_id=-900)
    user.telegram_chat_id = CHAT_ID
    db.commit()
    _mock_callback(monkeypatch, chat_id=CHAT_ID, intake_slug=INTAKE_TRIAL_SLUG)

    client.get("/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False)

    db.refresh(user)
    assert user.access_until is None


def test_staff_with_linked_telegram_gets_no_deadline(client, db, monkeypatch, user_factory):
    _make_link(db, is_active=True)
    curator = user_factory(vk_id=-901, role_name="куратор")
    curator.telegram_chat_id = CHAT_ID
    db.commit()
    _mock_callback(monkeypatch, chat_id=CHAT_ID, intake_slug=INTAKE_TRIAL_SLUG)

    client.get("/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False)

    db.refresh(curator)
    assert curator.access_until is None


def test_regular_login_without_intake_slug_gets_no_deadline(client, db, monkeypatch):
    _make_link(db, is_active=True)
    _mock_callback(monkeypatch, chat_id=CHAT_ID, intake_slug=None)

    client.get("/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False)

    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).first()
    assert user is not None
    assert user.access_until is None


def test_intake_slug_with_empty_deadline_creates_user_without_crash(client, db, monkeypatch):
    _make_link(db, is_active=False, access_until=None)
    _mock_callback(monkeypatch, chat_id=CHAT_ID, intake_slug=INTAKE_TRIAL_SLUG)

    resp = client.get(
        "/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False,
    )
    assert resp.status_code == 302

    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).first()
    assert user is not None
    assert user.access_until is None


def test_reactivated_archived_user_via_intake_gets_no_deadline(client, db, monkeypatch, user_factory):
    """Вернувшийся из архива (soft-delete) по ссылке набора — `_upsert_telegram_user`
    считает его существующим (`created=False`), реактивирует, но срок не ставит:
    «вернувшийся из архива срока не получит» (план, шаг 4)."""
    _make_link(db, is_active=True)
    user = user_factory(vk_id=-903)
    user.telegram_chat_id = CHAT_ID
    user.deleted_at = datetime.now(timezone.utc)
    user.is_active = False
    db.commit()
    _mock_callback(monkeypatch, chat_id=CHAT_ID, intake_slug=INTAKE_TRIAL_SLUG)

    client.get("/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False)

    db.refresh(user)
    assert user.deleted_at is None
    assert user.is_active is True
    assert user.access_until is None


# ---------------------------------------------------------------------------
# /cabinet/periods: выключатель и дата
# ---------------------------------------------------------------------------

def test_toggle_forbidden_for_student_and_curator(auth_client, client, db, session_factory, user_factory):
    student_client, _student = auth_client
    resp = student_client.post("/cabinet/intake/toggle", data={"csrf_token": "x"})
    assert resp.status_code == 403

    curator = user_factory(vk_id=-902, role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.post("/cabinet/intake/toggle", data={"csrf_token": "x"})
    assert resp.status_code == 403


def test_toggle_requires_real_csrf(admin_client):
    from app.dependencies import require_csrf
    from app.main import app

    client, _user = admin_client
    csrf_override = app.dependency_overrides.pop(require_csrf)
    try:
        resp = client.post("/cabinet/intake/toggle", data={})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides[require_csrf] = csrf_override


def test_toggle_without_deadline_rejected(admin_client):
    client, _user = admin_client
    resp = client.post("/cabinet/intake/toggle", data={"csrf_token": "x"})
    assert resp.status_code == 400


def test_deadline_roundtrip_msk_to_utc_to_form_field(admin_client, db):
    client, _user = admin_client
    resp = client.post(
        "/cabinet/intake/deadline",
        data={"csrf_token": "x", "deadline": "2026-09-27T23:30"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    link = db.query(IntakeLink).filter(IntakeLink.slug == INTAKE_TRIAL_SLUG).first()
    assert link is not None
    assert msk_input_value(link.access_until) == "2026-09-27T23:30"

    page = client.get("/cabinet/periods")
    assert 'value="2026-09-27T23:30"' in page.text


def test_deadline_garbage_rejected(admin_client):
    client, _user = admin_client
    resp = client.post(
        "/cabinet/intake/deadline",
        data={"csrf_token": "x", "deadline": "not-a-date"},
    )
    assert resp.status_code == 400


def test_toggle_can_now_activate_after_deadline_set(admin_client, db):
    client, _user = admin_client
    client.post(
        "/cabinet/intake/deadline",
        data={"csrf_token": "x", "deadline": "2026-09-27T23:30"},
    )
    resp = client.post("/cabinet/intake/toggle", data={"csrf_token": "x"}, follow_redirects=False)
    assert resp.status_code == 303

    link = db.query(IntakeLink).filter(IntakeLink.slug == INTAKE_TRIAL_SLUG).first()
    assert link.is_active is True
