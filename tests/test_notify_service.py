"""Тесты app/services/notify.py — учёт тумблера telegram_notifications_enabled.

Web Push и остальная логика диспетчера покрыта тестами роутов, создающих
Notification (см. session-handoffs). Здесь — только гейтинг Telegram-канала.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.models.notification import Notification
from app.services import notify as notify_module


def _telegram_transport(monkeypatch, *, reject_voice=False):
    telegram = notify_module.telegram_service
    requests = []

    def handle(request):
        requests.append(request)
        if reject_voice and request.url.path.endswith("/sendVoice"):
            return httpx.Response(400, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "123:test")
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")
    return requests


@pytest.mark.parametrize("enabled", [False, True])
def test_telegram_delivery_follows_user_settings(db, user_factory, monkeypatch, enabled):
    import json

    user = user_factory()
    user.telegram_chat_id = 777778
    user.telegram_notifications_enabled = enabled
    notice = Notification(user_id=user.id, title="Результат", text="85 баллов")
    db.add(notice)
    db.commit()
    requests = _telegram_transport(monkeypatch)
    asyncio.run(notify_module.notify(notice.id))
    assert len(requests) == int(enabled)
    if enabled:
        request, = requests
        assert request.url.path.endswith("/sendMessage")
        body = json.loads(request.content)
        assert str(body["chat_id"]) == str(user.telegram_chat_id)
        assert notice.text in body["text"]


VOICE_PATH = "point-a-audio/1/x.m4a"


def _voice_notification(db, user_factory, chat_id: int) -> Notification:
    user = user_factory()
    user.telegram_chat_id = chat_id
    db.commit()
    n = Notification(
        user_id=user.id, title="Точка А разобрана — уровень 1",
        text="Средний балл: 85 / 100.",
        audio_url=notify_module.s3_service.s3_public_url(VOICE_PATH),
    )
    db.add(n)
    db.commit()
    return n


def test_audio_url_sends_voice_file_instead_of_text(db, user_factory, monkeypatch):
    notice = _voice_notification(db, user_factory, 777779)
    requests = _telegram_transport(monkeypatch)
    monkeypatch.setattr(notify_module.s3_service, "download_from_s3", lambda path: b"source")
    monkeypatch.setattr(notify_module.media_transcode, "telegram_voice", lambda *args: ("x.ogg", b"opus-bytes", "audio/ogg"))
    asyncio.run(notify_module.notify(notice.id))
    request, = requests
    assert request.url.path.endswith("/sendVoice")
    assert b"opus-bytes" in request.content
    assert b"777779" in request.content


@pytest.mark.parametrize("downloaded", [True, False], ids=["telegram-refused", "storage-missing"])
def test_voice_failure_delivers_text(db, user_factory, monkeypatch, downloaded):
    import json

    notice = _voice_notification(db, user_factory, 777780)
    requests = _telegram_transport(monkeypatch, reject_voice=True)
    monkeypatch.setattr(notify_module.s3_service, "download_from_s3", lambda path: b"source" if downloaded else None)
    monkeypatch.setattr(notify_module.media_transcode, "telegram_voice", lambda *args: ("x.ogg", b"opus", "audio/ogg"))
    asyncio.run(notify_module.notify(notice.id))
    methods = [request.url.path.split("/")[-1] for request in requests]
    assert methods == (["sendVoice", "sendMessage"] if downloaded else ["sendMessage"])
    message = json.loads(requests[-1].content)
    assert str(message["chat_id"]) == "777780"
    assert notice.text in message["text"]


def test_send_voice_uploads_file_multipart(monkeypatch):
    """`sendVoice` получает сами байты полем `voice`, а не ссылку в JSON."""
    telegram = notify_module.telegram_service
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["type"] = request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    ok = asyncio.run(telegram.send_voice(
        42, b"opus-bytes", filename="x.ogg", content_type="audio/ogg", caption="Уровень 1",
    ))

    assert ok is True
    assert seen["path"].endswith("/sendVoice")
    assert seen["type"].startswith("multipart/form-data")
    body = seen["body"]
    assert b'name="voice"; filename="x.ogg"' in body
    assert b"Content-Type: audio/ogg" in body
    assert b"opus-bytes" in body
    assert b'name="chat_id"' in body and b"42" in body
    assert "Уровень 1".encode() in body


def _capture_telegram(monkeypatch, result):
    telegram = notify_module.telegram_service
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["type"] = request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True, "result": result})

    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return telegram, seen


def test_send_media_group_uploads_files_caption_on_first(monkeypatch):
    """Альбом — файлами через `attach://`, подпись только у первого фото,
    в ответ — id первого сообщения альбома (на него отвечает текст оценки)."""
    telegram, seen = _capture_telegram(monkeypatch, [{"message_id": 11}, {"message_id": 12}])

    first_id = asyncio.run(telegram.send_media_group(
        -100500, [("a.jpg", b"jpeg-a"), ("b.jpg", b"jpeg-b")],
        caption="<b>Оценка</b>", message_thread_id=77,
    ))

    assert first_id == 11
    assert seen["path"].endswith("/sendMediaGroup")
    assert seen["type"].startswith("multipart/form-data")
    body = seen["body"]
    assert b'name="photo0"; filename="a.jpg"' in body and b"jpeg-a" in body
    assert b'name="photo1"; filename="b.jpg"' in body and b"jpeg-b" in body
    assert b'name="message_thread_id"' in body and b"77" in body
    media_start = body.index(b'name="media"')
    media = body[media_start:body.index(b"--", media_start)].decode()
    assert media.count("attach://") == 2
    assert media.count('"caption"') == 1


def test_send_photo_returns_message_id(monkeypatch):
    telegram, seen = _capture_telegram(monkeypatch, {"message_id": 21})

    assert asyncio.run(telegram.send_photo(-100500, b"jpeg", filename="a.jpg", caption="x")) == 21
    assert seen["path"].endswith("/sendPhoto")
    assert b'name="photo"; filename="a.jpg"' in seen["body"]


def test_send_message_reply_to(monkeypatch):
    telegram, seen = _capture_telegram(monkeypatch, {"message_id": 31})

    assert asyncio.run(telegram.send_message(-100500, "текст", reply_to_message_id=11)) is True
    assert b'"reply_parameters":{"message_id":11' in seen["body"].replace(b" ", b"")


# ── пачка без удержания базы (прод-инцидент 04.10.2026) ─────────────────────
#
# Публикация видеоурока разослала 90 уведомлений разом: все шли параллельно и
# держали соединение с базой, пока ждали Telegram и push. Пул воркера кончился,
# сохранение заданий падало, пока приложение не перезапустили.

def _telegram_notifications(db, user_factory, count, base_chat=880_000):
    ids = []
    for index in range(count):
        user = user_factory(vk_id=base_chat + index)
        user.telegram_chat_id = base_chat + index
        user.telegram_notifications_enabled = True
        db.commit()
        n = Notification(user_id=user.id, title="Новый видеоурок", text="")
        db.add(n)
        db.commit()
        ids.append(n.id)
    return ids


def _counting_sessions(monkeypatch):
    """Подменяет фабрику сессий рассылки счётчиком открытых сессий."""
    real = notify_module.SessionLocal
    state = {"open": 0}

    def factory():
        session = real()
        state["open"] += 1
        close = session.close

        def counted_close():
            state["open"] -= 1
            close()

        session.close = counted_close
        return session

    monkeypatch.setattr(notify_module, "SessionLocal", factory)
    return state


def test_db_session_is_closed_while_waiting_for_network(db, user_factory, monkeypatch):
    ids = _telegram_notifications(db, user_factory, 3)
    sessions = _counting_sessions(monkeypatch)
    seen_open = []

    async def send_message(chat_id, text):
        seen_open.append(sessions["open"])
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(notify_module.telegram_service, "send_message", send_message)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")

    asyncio.run(notify_module.notify_many(ids))

    assert seen_open == [0, 0, 0]
    assert sessions["open"] == 0


def test_batch_is_sent_with_limited_concurrency(db, user_factory, monkeypatch):
    base_chat = 880000
    count = notify_module.NOTIFY_CONCURRENCY * 3
    ids = _telegram_notifications(db, user_factory, count, base_chat=base_chat)
    delivered = []
    state = {"now": 0, "peak": 0}

    async def send_message(chat_id, text):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        delivered.append(chat_id)
        return True

    monkeypatch.setattr(notify_module.telegram_service, "send_message", send_message)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")

    asyncio.run(notify_module.notify_many(ids))

    assert sorted(delivered) == list(range(base_chat, base_chat + count))
    assert 1 < state["peak"] <= notify_module.NOTIFY_CONCURRENCY


def _push_subscription(db, user_factory):
    from app.models.push_subscription import PushSubscription

    user = user_factory(vk_id=881_000)
    sub = PushSubscription(
        user_id=user.id, endpoint="https://fcm.googleapis.com/abc", p256dh="k", auth_key="a",
    )
    db.add(sub)
    n = Notification(user_id=user.id, title="Тест", text="")
    db.add(n)
    db.commit()
    return sub, n


def test_push_has_timeout(db, user_factory, monkeypatch):
    _sub, n = _push_subscription(db, user_factory)
    push = AsyncMock()
    monkeypatch.setattr(notify_module, "webpush_async", push)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")

    asyncio.run(notify_module.notify(n.id))

    assert push.await_args.kwargs["timeout"] > 0  # без срока зависший пуш держит рассылку


def test_push_survives_sleeping_device(db, user_factory, monkeypatch):
    """`pywebpush` по умолчанию шлёт TTL=0: спящий телефон такой пуш теряет."""
    _sub, n = _push_subscription(db, user_factory)
    push = AsyncMock()
    monkeypatch.setattr(notify_module, "webpush_async", push)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")

    asyncio.run(notify_module.notify(n.id))

    assert push.await_args.kwargs["ttl"] == notify_module.PUSH_TTL_SEC > 0
    assert push.await_args.kwargs["headers"] == {"Urgency": "high"}


def test_push_link_is_absolute_on_current_domain(db, user_factory, monkeypatch):
    """Подписка, оформленная на прежнем домене, достраивает путь своим адресом:
    после переезда 11.10.2026 нажатие вело бы на мёртвый apparchi.ru."""
    import json

    _sub, n = _push_subscription(db, user_factory)
    push = AsyncMock()
    monkeypatch.setattr(notify_module, "webpush_async", push)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")
    monkeypatch.setattr(notify_module.settings, "domain", "new.example")

    asyncio.run(notify_module.notify(n.id))

    assert json.loads(push.await_args.kwargs["data"])["url"] == "https://new.example/cabinet/notifications"


def test_gone_push_subscription_is_deactivated(db, user_factory, monkeypatch):
    sub, n = _push_subscription(db, user_factory)
    # Ответ как у aiohttp: код в `.status`, `.status_code` нет. До 12.10.2026
    # тест подсовывал `status_code`, а прод на первом же отказе падал с
    # AttributeError и мёртвые подписки не гасил.
    gone = notify_module.WebPushException("gone", response=Mock(spec=["status"], status=410))
    monkeypatch.setattr(notify_module, "webpush_async", AsyncMock(side_effect=gone))
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")

    asyncio.run(notify_module.notify(n.id))

    db.expire_all()
    assert sub.is_active is False


def test_push_refusal_keeps_subscription_and_does_not_crash(db, user_factory, monkeypatch):
    sub, n = _push_subscription(db, user_factory)
    refused = notify_module.WebPushException("bad", response=Mock(spec=["status"], status=400))
    push = AsyncMock(side_effect=refused)
    monkeypatch.setattr(notify_module, "webpush_async", push)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")
    errors = []
    monkeypatch.setattr(notify_module.logger, "exception", lambda *a, **k: errors.append(a))

    asyncio.run(notify_module.notify(n.id))

    db.expire_all()
    assert sub.is_active is True
    assert errors == []


def test_push_network_error_keeps_subscription(db, user_factory, monkeypatch):
    sub, n = _push_subscription(db, user_factory)
    monkeypatch.setattr(
        notify_module, "webpush_async", AsyncMock(side_effect=asyncio.TimeoutError()),
    )
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")

    asyncio.run(notify_module.notify(n.id))

    db.expire_all()
    assert sub.is_active is True
