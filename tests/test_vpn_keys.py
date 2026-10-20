"""Свой VPN для учеников: запрос ключа с экрана входа, отправка ботом и сверка
(`app/services/vpn_keys.py`, `app/api/auth.py::vpn_key_request`).

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
    assert vpn_keys.label_for(student) == f"Арина Губанова @arriri #{student.id} устр. 1"
    assert vpn_keys.label_for(student, 2).endswith("устр. 2")


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


# ── запрос с экрана входа ────────────────────────────────────────────────────

def _send(nick: str) -> None:
    asyncio.run(vpn_keys.send_key_by_username(nick))


def test_send_by_username_delivers_key_and_links(db, student, service, sent):
    _send("@Arriri")
    assert len(sent) == 1 and sent[0]["chat_id"] == CHAT_ID
    text = sent[0]["text"]
    assert "<code>vpn://key-1</code>" in text
    for url in (vpn_keys.ANDROID_URL, vpn_keys.IOS_URL, vpn_keys.DESKTOP_URL, vpn_keys.DESKTOP_MIRROR_URL):
        assert f'href="{url}"' in text
    assert "DefaultVPN" in text and "AmneziaVPN" in text
    assert service.created == 1


def test_send_by_username_reuses_key(db, student, service, sent):
    _send("arriri")
    _send("arriri")
    assert service.created == 1
    assert ["<code>vpn://key-1</code>" in m["text"] for m in sent] == [True, True]
    assert all(m["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "vpn:device2" for m in sent)
    assert "Один ключ – одно устройство" in sent[0]["text"]


def test_send_by_username_respects_cooldown(db, student, service, sent, monkeypatch):
    import app.cache as cache
    claims = iter([True, False])
    monkeypatch.setattr(cache, "claim_vpn_key_send", lambda user_id, ttl: next(claims))
    _send("arriri")
    _send("arriri")
    assert len(sent) == 1


def test_send_by_username_unknown_nick_sends_nothing(db, student, service, sent):
    _send("@someone_else")
    assert sent == [] and service.created == 0


def test_send_by_username_unlinked_student_sends_nothing(db, student, service, sent):
    student.telegram_chat_id = None
    db.commit()
    _send("arriri")
    assert sent == [] and service.created == 0


def test_send_by_username_archived_sends_nothing(db, student, service, sent):
    student.archived_at = datetime.now(timezone.utc)
    student.is_active = False
    db.commit()
    _send("arriri")
    assert sent == [] and service.created == 0


def test_send_by_username_service_down_says_so(db, student, service, sent):
    service.down = True
    _send("arriri")
    assert len(sent) == 1 and sent[0]["text"] == vpn_keys.UNAVAILABLE_MESSAGE
    assert db.query(VpnKey).count() == 0


def test_request_route_answers_the_same_for_any_nick(client, service, monkeypatch):
    calls = []

    async def fake_send(nick):
        calls.append(nick)

    monkeypatch.setattr(vpn_keys, "send_key_by_username", fake_send)
    for nick in ("@arriri", "@nobody"):
        resp = client.post("/auth/vpn-request", data={"tg_username": nick}, follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/?vpn=sent"
    assert calls == ["@arriri", "@nobody"]


def test_request_route_does_nothing_when_disabled(client, monkeypatch):
    monkeypatch.setattr(_app_settings, "vpn_keys_url", "")
    calls = []

    async def fake_send(nick):
        calls.append(nick)

    monkeypatch.setattr(vpn_keys, "send_key_by_username", fake_send)
    resp = client.post("/auth/vpn-request", data={"tg_username": "@arriri"}, follow_redirects=False)
    assert resp.status_code == 303 and calls == []


def test_bot_no_longer_answers_vpn_command(client, db, student, service, sent):
    resp = client.post(
        "/auth/telegram/webhook",
        json={"update_id": 1, "message": {"chat": {"id": CHAT_ID}, "text": "/vpn",
                                           "from": {"id": CHAT_ID, "first_name": "Арина"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )
    assert resp.status_code == 200
    assert sent == [] and service.created == 0


# ── второе устройство ───────────────────────────────────────────────────────

@pytest.fixture()
def answers(monkeypatch):
    calls: list[str] = []

    async def fake_answer(callback_id, text):
        calls.append(text)

    monkeypatch.setattr(auth_module.broadcast_delivery, "answer_callback", fake_answer)
    return calls


def _press_second(client, *, from_id=CHAT_ID, chat_id=CHAT_ID):
    return client.post(
        "/auth/telegram/webhook",
        json={"update_id": 2, "callback_query": {
            "id": "cb1", "from": {"id": from_id}, "data": "vpn:device2",
            "message": {"message_id": 10, "chat": {"id": chat_id}},
        }},
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )


def test_second_device_key_by_button(client, db, student, service, sent, answers):
    _send("arriri")
    assert _press_second(client).status_code == 200
    assert service.created == 2
    assert "<code>vpn://key-2</code>" in sent[-1]["text"]
    assert "устройство 2 из 2" in sent[-1]["text"] and "последний" in sent[-1]["text"]
    assert "ниже" in answers[-1]
    keys = vpn_keys.live_keys(db, student.id)
    assert [k.device for k in keys] == [1, 2]
    # Повторное нажатие — тот же второй ключ, третьего нет.
    _press_second(client)
    assert service.created == 2 and "<code>vpn://key-2</code>" in sent[-1]["text"]


def test_site_request_resends_both_keys_without_button(client, db, student, service, sent, answers):
    _send("arriri")
    _press_second(client)
    sent.clear()
    _send("arriri")
    assert service.created == 2
    assert ["vpn://key-1" in sent[0]["text"], "vpn://key-2" in sent[1]["text"]] == [True, True]
    assert sent[0]["reply_markup"] is None


def test_no_third_key(db, student, service):
    with pytest.raises(ValueError):
        asyncio.run(vpn_keys.get_or_issue(db, student, 3))


def test_second_device_needs_first_key(client, db, student, service, sent, answers):
    _press_second(client)
    assert service.created == 0 and sent == []
    assert "Сначала" in answers[-1]


def test_second_device_button_from_foreign_chat_ignored(client, db, student, service, sent, answers):
    _send("arriri")
    _press_second(client, from_id=CHAT_ID + 1)
    assert service.created == 1


def test_second_device_refused_after_archive(client, db, student, service, sent, answers):
    _send("arriri")
    student.archived_at = datetime.now(timezone.utc)
    student.is_active = False
    db.commit()
    _press_second(client)
    assert service.created == 1 and "поддержку" in answers[-1]
