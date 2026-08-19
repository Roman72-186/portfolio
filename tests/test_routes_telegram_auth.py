"""Tests for the direct Telegram bot login integration (webhook, /start,
staff-issued linking for existing students) — see app/api/auth.py (Telegram
bot login section) and
app/services/auth_links.py::issue_telegram_link_token/consume_telegram_link_token.

No tariff dialog in the bot itself: a confirmed member is provisioned and
logged in immediately — /cabinet/profile (needs_profile_setup) picks up the
rest (name, phone, tariff) on first cabinet visit, same as it already does
for manually-created VK accounts.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.api.auth as auth_module
from app.config import settings as _app_settings
from app.models.telegram_link_token import TelegramLinkToken
from app.models.user import User
from app.services.auth_links import issue_telegram_link_token

WEBHOOK_SECRET = "test-webhook-secret"
CHAT_ID = 555_000_111


def _headers(secret: str | None = WEBHOOK_SECRET) -> dict:
    if secret is None:
        return {}
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def _start_update(chat_id: int, *, text: str = "/start", user_id: int | None = None,
                   username: str | None = "tguser", first_name: str = "Ира") -> dict:
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
            "from": {
                "id": user_id or chat_id,
                "username": username,
                "first_name": first_name,
                "last_name": "Иванова",
            },
        },
    }


@pytest.fixture(autouse=True)
def _telegram_settings(monkeypatch):
    monkeypatch.setattr(_app_settings, "telegram_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(_app_settings, "telegram_bot_token", "test-bot-token")
    monkeypatch.setattr(_app_settings, "telegram_channel_id", -100123456)
    monkeypatch.setattr(_app_settings, "telegram_bot_username", "test_apparchi_bot")
    monkeypatch.setattr(_app_settings, "domain", "apparchi.ru")


@pytest.fixture()
def sent_messages(monkeypatch):
    """Capture every telegram_service.send_message call instead of hitting the network."""
    calls: list[dict] = []

    async def fake_send_message(chat_id, text, *, reply_markup=None):
        calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return True

    monkeypatch.setattr(auth_module.telegram_service, "send_message", fake_send_message)
    return calls


def _mock_membership(monkeypatch, value):
    async def fake_check(user_id):
        return value
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check)


# ---------------------------------------------------------------------------
# Webhook secret enforcement
# ---------------------------------------------------------------------------

def test_webhook_rejects_missing_secret_header(client):
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers(None))
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret_header(client):
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers("wrong"))
    assert resp.status_code == 401


def test_webhook_503_when_secret_not_configured(client, monkeypatch):
    monkeypatch.setattr(_app_settings, "telegram_webhook_secret", "")
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers("anything"))
    assert resp.status_code == 503


def test_webhook_malformed_body_does_not_500(client):
    resp = client.post("/auth/telegram/webhook", json={"not": "an update"}, headers=_headers())
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# New student sign-up: /start without payload
# ---------------------------------------------------------------------------

def test_new_start_not_member_sends_support_message_and_creates_no_user(client, db, monkeypatch, sent_messages):
    _mock_membership(monkeypatch, False)
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers())
    assert resp.status_code == 200
    assert db.query(User).filter(User.telegram_chat_id == CHAT_ID).first() is None
    assert len(sent_messages) == 1
    assert "поддержк" in sent_messages[0]["text"].lower()
    assert sent_messages[0]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://t.me/roman_chatbots"


def test_new_start_membership_inconclusive_does_not_deny_or_create_user(client, db, monkeypatch, sent_messages):
    _mock_membership(monkeypatch, None)
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers())
    assert resp.status_code == 200
    assert db.query(User).filter(User.telegram_chat_id == CHAT_ID).first() is None
    assert "попробуйте" in sent_messages[0]["text"].lower()


def test_new_start_member_creates_user_immediately_no_tariff_dialog(client, db, monkeypatch, sent_messages):
    """Подтверждённый member заводится и логинится без диалога с ботом —
    тариф и остальные поля соберёт анкета /cabinet/profile при первом
    визите в кабинет (needs_profile_setup в cabinet_student.py)."""
    _mock_membership(monkeypatch, True)

    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers())
    assert resp.status_code == 200

    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).first()
    assert user is not None
    assert user.tariff == "УВЕРЕННЫЙ"  # дефолт модели, анкета переопределит
    assert user.profile_completed is False  # анкета ещё не заполнена
    assert user.vk_id < 0  # синтетическая идентичность, next_manual_vk_id
    assert len(sent_messages) == 1
    assert "ссылка" in sent_messages[0]["text"].lower()


def test_new_start_existing_linked_chat_id_relogs_in_instead_of_duplicating(client, db, monkeypatch, sent_messages, user_factory):
    user = user_factory(vk_id=-777, is_group_member=False)
    user.telegram_chat_id = CHAT_ID
    db.commit()

    _mock_membership(monkeypatch, True)
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers())
    assert resp.status_code == 200

    assert db.query(User).filter(User.telegram_chat_id == CHAT_ID).count() == 1
    db.refresh(user)
    assert user.is_group_member is True
    assert "ссылка" in sent_messages[-1]["text"].lower()


# ---------------------------------------------------------------------------
# Staff-issued linking for existing students (history must be preserved)
# ---------------------------------------------------------------------------

def test_link_start_valid_token_links_existing_user_preserving_history(client, db, monkeypatch, sent_messages, user_factory):
    existing = user_factory(vk_id=42_001, name="Старый Ученик")
    existing_id = existing.id
    raw_token, _link = issue_telegram_link_token(db, user=existing, issued_by="test")

    _mock_membership(monkeypatch, True)
    resp = client.post(
        "/auth/telegram/webhook",
        json=_start_update(CHAT_ID, text=f"/start {raw_token}"),
        headers=_headers(),
    )
    assert resp.status_code == 200

    db.refresh(existing)
    assert existing.telegram_chat_id == CHAT_ID
    assert existing.id == existing_id  # same account, not a new one
    assert db.query(User).count() == 1
    assert "ссылка" in sent_messages[-1]["text"].lower()


def test_link_start_invalid_token_sends_error(client, db, sent_messages):
    resp = client.post(
        "/auth/telegram/webhook",
        json=_start_update(CHAT_ID, text="/start not-a-real-token"),
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert db.query(User).filter(User.telegram_chat_id == CHAT_ID).first() is None
    assert "недействительна" in sent_messages[-1]["text"].lower()


def test_link_start_expired_token_sends_error(client, db, sent_messages, user_factory):
    existing = user_factory(vk_id=42_002)
    raw_token, link_token = issue_telegram_link_token(db, user=existing, issued_by="test")
    link_token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    resp = client.post(
        "/auth/telegram/webhook",
        json=_start_update(CHAT_ID, text=f"/start {raw_token}"),
        headers=_headers(),
    )
    assert resp.status_code == 200
    db.refresh(existing)
    assert existing.telegram_chat_id is None
    assert "истекла" in sent_messages[-1]["text"].lower()


def test_link_start_already_used_token_sends_error(client, db, sent_messages, user_factory, monkeypatch):
    existing = user_factory(vk_id=42_003)
    raw_token, _link = issue_telegram_link_token(db, user=existing, issued_by="test")

    _mock_membership(monkeypatch, True)
    first = client.post(
        "/auth/telegram/webhook",
        json=_start_update(CHAT_ID, text=f"/start {raw_token}"),
        headers=_headers(),
    )
    assert first.status_code == 200

    second = client.post(
        "/auth/telegram/webhook",
        json=_start_update(CHAT_ID + 1, text=f"/start {raw_token}"),
        headers=_headers(),
    )
    assert second.status_code == 200
    assert "использована" in sent_messages[-1]["text"].lower()
    db.refresh(existing)
    assert existing.telegram_chat_id == CHAT_ID  # unchanged by the replay attempt


def test_link_start_chat_already_linked_to_another_user_is_rejected(client, db, sent_messages, user_factory, monkeypatch):
    already_linked = user_factory(vk_id=42_004)
    already_linked.telegram_chat_id = CHAT_ID
    db.commit()

    target = user_factory(vk_id=42_005)
    raw_token, _link = issue_telegram_link_token(db, user=target, issued_by="test")

    _mock_membership(monkeypatch, True)
    resp = client.post(
        "/auth/telegram/webhook",
        json=_start_update(CHAT_ID, text=f"/start {raw_token}"),
        headers=_headers(),
    )
    assert resp.status_code == 200
    db.refresh(target)
    assert target.telegram_chat_id is None
    assert "уже привязан" in sent_messages[-1]["text"].lower()


# ---------------------------------------------------------------------------
# Fail-closed gate on re-login: student loses channel membership
# ---------------------------------------------------------------------------

def test_relogin_student_no_longer_member_is_denied_login_link(client, db, sent_messages, user_factory, monkeypatch):
    student = user_factory(vk_id=42_006, role_name="ученик", is_group_member=True)
    student.telegram_chat_id = CHAT_ID
    db.commit()

    _mock_membership(monkeypatch, False)
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers())
    assert resp.status_code == 200

    db.refresh(student)
    assert student.is_group_member is False
    assert "ссылка" not in sent_messages[-1]["text"].lower()
    assert "канал" in sent_messages[-1]["text"].lower()


def test_relogin_curator_bypasses_membership_gate(client, db, sent_messages, user_factory, monkeypatch):
    curator = user_factory(vk_id=42_007, role_name="куратор", is_group_member=True)
    curator.telegram_chat_id = CHAT_ID
    db.commit()

    _mock_membership(monkeypatch, False)
    resp = client.post("/auth/telegram/webhook", json=_start_update(CHAT_ID), headers=_headers())
    assert resp.status_code == 200

    db.refresh(curator)
    assert curator.is_group_member is False  # flag updated honestly...
    assert "ссылка" in sent_messages[-1]["text"].lower()  # ...but staff still gets in
