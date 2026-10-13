"""Свой VPN для учеников: выдача ключа ботом по /vpn и сверка
(`app/services/vpn_keys.py`, `app/api/auth.py::_handle_vpn_request`).

Служба ключей на VPN-сервере подменена `httpx.MockTransport` со словарём
пиров — сеть не нужна, контракт тот же, что у `deploy/vpn_keys/`.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app.api.auth as auth_module
from app.config import settings as _app_settings
from app.models.vpn_key import VpnKey
from app.services import vpn_keys

WEBHOOK_SECRET = "test-webhook-secret"
CHAT_ID = 777_000_222


class FakeService:
    def __init__(self):
        self.peers: dict[str, dict] = {}
        self.created = 0
        self.down = False

    def add_old(self, peer_id: str, minutes_ago: int = 60) -> None:
        created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        self.peers[peer_id] = {"label": "x", "created_at": created.isoformat()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down", request=request)
        assert request.headers["Authorization"] == "Bearer test-vpn-token"
        if request.method == "POST":
            self.created += 1
            peer_id = f"PUB{self.created}="
            self.peers[peer_id] = {"label": "x", "created_at": datetime.now(timezone.utc).isoformat()}
            return httpx.Response(201, json={"peer_id": peer_id, "key": f"vpn://key-{self.created}"})
        if request.method == "DELETE":
            peer_id = request.url.params["peer_id"]
            return httpx.Response(200, json={"deleted": self.peers.pop(peer_id, None) is not None})
        return httpx.Response(200, json={"peers": [
            {"peer_id": pid, **info} for pid, info in self.peers.items()
        ]})


@pytest.fixture()
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(_app_settings, "vpn_keys_url", "https://vpn.test:8443")
    monkeypatch.setattr(_app_settings, "vpn_keys_token", "test-vpn-token")
    monkeypatch.setattr(vpn_keys, "_client", lambda: httpx.AsyncClient(
        base_url="https://vpn.test:8443",
        headers={"Authorization": "Bearer test-vpn-token"},
        transport=httpx.MockTransport(fake.handler),
    ))
    return fake


@pytest.fixture(autouse=True)
def _telegram_settings(monkeypatch):
    monkeypatch.setattr(_app_settings, "telegram_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(_app_settings, "telegram_bot_token", "test-bot-token")


@pytest.fixture()
def sent(monkeypatch):
    calls: list[dict] = []

    async def fake_send_message(chat_id, text, *, reply_markup=None):
        calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return True

    monkeypatch.setattr(auth_module.telegram_service, "send_message", fake_send_message)
    return calls


@pytest.fixture()
def student(db, user_factory):
    user = user_factory(vk_id=300_001, name="Арина Губанова")
    user.telegram_chat_id = CHAT_ID
    user.tg_username = "arriri"
    db.commit()
    return user


def _vpn(client, text="/vpn"):
    return client.post(
        "/auth/telegram/webhook",
        json={"update_id": 1, "message": {"chat": {"id": CHAT_ID}, "text": text,
                                           "from": {"id": CHAT_ID, "first_name": "Арина"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )


# ── право ───────────────────────────────────────────────────────────────────

def test_eligibility(db, student):
    assert vpn_keys.is_eligible(student)
    student.access_until = datetime.now(timezone.utc) - timedelta(days=1)
    assert vpn_keys.is_eligible(student), "истёкший доступ ключ не отнимает"
    student.archived_at = datetime.now(timezone.utc)
    assert not vpn_keys.is_eligible(student)
    student.archived_at = None
    student.is_active = False
    assert not vpn_keys.is_eligible(student)
    student.is_active = True
    student.deleted_at = datetime.now(timezone.utc)
    assert not vpn_keys.is_eligible(student)


def test_label_has_name_nick_and_id(student):
    assert vpn_keys.label_for(student) == f"Арина Губанова @arriri #{student.id}"


# ── выдача ──────────────────────────────────────────────────────────────────

def test_get_or_issue_reuses_live_key(db, student, service):
    first = asyncio.run(vpn_keys.get_or_issue(db, student))
    second = asyncio.run(vpn_keys.get_or_issue(db, student))
    assert first == second == "vpn://key-1"
    assert service.created == 1
    row = db.query(VpnKey).one()
    assert row.peer_id == "PUB1=" and row.revoked_at is None


def test_get_or_issue_raises_when_disabled(db, student, monkeypatch):
    monkeypatch.setattr(_app_settings, "vpn_keys_url", "")
    with pytest.raises(vpn_keys.VpnKeysUnavailable):
        asyncio.run(vpn_keys.get_or_issue(db, student))


def test_revoke_then_new_key(db, student, service):
    asyncio.run(vpn_keys.get_or_issue(db, student))
    asyncio.run(vpn_keys.revoke(db, vpn_keys.live_key(db, student.id)))
    assert service.peers == {}
    assert vpn_keys.live_key(db, student.id) is None
    assert asyncio.run(vpn_keys.get_or_issue(db, student)) == "vpn://key-2"


# ── сверка ──────────────────────────────────────────────────────────────────

def test_reconcile_revokes_archived_student(db, student, service):
    asyncio.run(vpn_keys.get_or_issue(db, student))
    student.archived_at = datetime.now(timezone.utc)
    db.commit()
    stats = asyncio.run(vpn_keys.reconcile(db))
    assert stats["revoked"] == 1
    assert service.peers == {}
    assert vpn_keys.live_key(db, student.id) is None


def test_reconcile_marks_key_gone_when_owner_deleted_peer(db, student, service):
    asyncio.run(vpn_keys.get_or_issue(db, student))
    service.peers.clear()
    stats = asyncio.run(vpn_keys.reconcile(db))
    assert stats["gone"] == 1
    assert vpn_keys.live_key(db, student.id) is None


def test_reconcile_removes_old_orphans_only(db, student, service):
    service.add_old("OLD=", minutes_ago=60)
    service.add_old("FRESH=", minutes_ago=1)
    stats = asyncio.run(vpn_keys.reconcile(db))
    assert stats["orphans"] == 1
    assert set(service.peers) == {"FRESH="}


def test_reconcile_survives_service_down(db, student, service):
    asyncio.run(vpn_keys.get_or_issue(db, student))
    student.is_active = False
    db.commit()
    service.down = True
    stats = asyncio.run(vpn_keys.reconcile(db))
    assert stats["errors"] >= 1
    assert vpn_keys.live_key(db, student.id) is not None, "отзыв добьёт следующая сверка"


# ── бот ─────────────────────────────────────────────────────────────────────

def test_bot_gives_key_to_linked_student(client, db, student, service, sent):
    assert _vpn(client).status_code == 200
    assert len(sent) == 1
    assert "<code>vpn://key-1</code>" in sent[0]["text"]
    assert "DefaultVPN" in sent[0]["text"] and "AmneziaVPN" in sent[0]["text"]
    _vpn(client, "/vpn@test_apparchi_bot")
    assert service.created == 1, "повторный /vpn — тот же ключ"
    assert "<code>vpn://key-1</code>" in sent[1]["text"]


def test_bot_refuses_unknown_chat(client, db, service, sent):
    _vpn(client)
    assert service.created == 0
    assert "не привязан" in sent[0]["text"]
    assert sent[0]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://t.me/roman_chatbots"


def test_bot_refuses_archived(client, db, student, service, sent):
    student.archived_at = datetime.now(timezone.utc)
    student.is_active = False
    db.commit()
    _vpn(client)
    assert service.created == 0
    assert "отключён" in sent[0]["text"]


def test_bot_service_down_answers_politely(client, db, student, service, sent):
    service.down = True
    assert _vpn(client).status_code == 200
    assert "не получается" in sent[0]["text"]
    assert db.query(VpnKey).count() == 0


def test_login_page_shows_vpn_hint(client, monkeypatch):
    monkeypatch.setattr(_app_settings, "telegram_bot_username", "test_apparchi_bot")
    monkeypatch.setattr(auth_module, "_telegram_login_enabled", lambda: True)
    resp = client.get("/")  # экран входа ученика — login.html (`entry_point`)
    assert resp.status_code == 200
    assert "/vpn" in resp.text
    assert "https://t.me/test_apparchi_bot" in resp.text
