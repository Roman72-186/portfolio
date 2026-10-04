"""Тесты app/services/notify.py — учёт тумблера telegram_notifications_enabled.

Web Push и остальная логика диспетчера покрыта тестами роутов, создающих
Notification (см. session-handoffs). Здесь — только гейтинг Telegram-канала.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import httpx

from app.models.notification import Notification
from app.services import notify as notify_module


def test_telegram_skipped_when_disabled(db, user_factory, monkeypatch):
    user = user_factory()
    user.telegram_chat_id = 777_777
    user.telegram_notifications_enabled = False
    db.commit()

    n = Notification(user_id=user.id, title="Тест", text="")
    db.add(n)
    db.commit()

    send_mock = AsyncMock()
    monkeypatch.setattr(notify_module.telegram_service, "send_message", send_mock)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")

    asyncio.run(notify_module.notify(n.id))

    send_mock.assert_not_called()


def test_telegram_sent_when_enabled(db, user_factory, monkeypatch):
    user = user_factory()
    user.telegram_chat_id = 777_778
    db.commit()
    assert user.telegram_notifications_enabled is True

    n = Notification(user_id=user.id, title="Тест", text="Текст")
    db.add(n)
    db.commit()

    send_mock = AsyncMock()
    monkeypatch.setattr(notify_module.telegram_service, "send_message", send_mock)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")

    asyncio.run(notify_module.notify(n.id))

    send_mock.assert_called_once_with(777_778, "Тест\n\nТекст")


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


def _telegram_mocks(monkeypatch, *, voice_ok=True, downloaded=b"aac-bytes"):
    voice_mock = AsyncMock(return_value=voice_ok)
    message_mock = AsyncMock(return_value=True)
    download = Mock(return_value=downloaded)
    transcode = Mock(return_value=("x.ogg", b"opus-bytes", "audio/ogg"))
    monkeypatch.setattr(notify_module.telegram_service, "send_voice", voice_mock)
    monkeypatch.setattr(notify_module.telegram_service, "send_message", message_mock)
    monkeypatch.setattr(notify_module.s3_service, "download_from_s3", download)
    monkeypatch.setattr(notify_module.media_transcode, "telegram_voice", transcode)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")
    return voice_mock, message_mock, download, transcode


CAPTION = "Точка А разобрана — уровень 1\n\nСредний балл: 85 / 100."


def test_audio_url_sends_voice_file_instead_of_text(db, user_factory, monkeypatch):
    """Точка А: уведомление с `audio_url` уходит `sendVoice` загрузкой файла
    OGG/Opus, а не ссылкой — по ссылке Telegram показывает голосовым только
    ogg до 1 МБ, остальное приходит документом. Caption несёт тот же текст."""
    n = _voice_notification(db, user_factory, 777_779)
    voice_mock, message_mock, download, transcode = _telegram_mocks(monkeypatch)

    asyncio.run(notify_module.notify(n.id))

    download.assert_called_once_with(VOICE_PATH)
    transcode.assert_called_once_with("x.m4a", b"aac-bytes", "audio/mp4")
    voice_mock.assert_called_once_with(
        777_779, b"opus-bytes", filename="x.ogg", content_type="audio/ogg", caption=CAPTION,
    )
    message_mock.assert_not_called()


def test_voice_rejected_falls_back_to_text(db, user_factory, monkeypatch):
    """Telegram не принял голосовое — уровень ученик всё равно узнаёт текстом."""
    n = _voice_notification(db, user_factory, 777_780)
    voice_mock, message_mock, _, _ = _telegram_mocks(monkeypatch, voice_ok=False)

    asyncio.run(notify_module.notify(n.id))

    voice_mock.assert_called_once()
    message_mock.assert_called_once_with(777_780, CAPTION)


def test_voice_not_downloaded_falls_back_to_text(db, user_factory, monkeypatch):
    n = _voice_notification(db, user_factory, 777_781)
    voice_mock, message_mock, _, transcode = _telegram_mocks(monkeypatch, downloaded=None)

    asyncio.run(notify_module.notify(n.id))

    transcode.assert_not_called()
    voice_mock.assert_not_called()
    message_mock.assert_called_once_with(777_781, CAPTION)


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
    ids = _telegram_notifications(db, user_factory, notify_module.NOTIFY_CONCURRENCY * 3)
    state = {"now": 0, "peak": 0, "sent": 0}

    async def send_message(chat_id, text):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        state["sent"] += 1
        return True

    monkeypatch.setattr(notify_module.telegram_service, "send_message", send_message)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")

    asyncio.run(notify_module.notify_many(ids))

    assert state["sent"] == len(ids)
    assert state["peak"] == notify_module.NOTIFY_CONCURRENCY


def _push_subscription(db, user_factory):
    from app.models.push_subscription import PushSubscription

    user = user_factory(vk_id=881_000)
    sub = PushSubscription(
        user_id=user.id, endpoint="https://push.example/abc", p256dh="k", auth_key="a",
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

    assert push.await_args.kwargs["timeout"] == notify_module.PUSH_TIMEOUT_SEC


def test_gone_push_subscription_is_deactivated(db, user_factory, monkeypatch):
    sub, n = _push_subscription(db, user_factory)
    gone = notify_module.WebPushException("gone", response=Mock(status_code=410))
    monkeypatch.setattr(notify_module, "webpush_async", AsyncMock(side_effect=gone))
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")

    asyncio.run(notify_module.notify(n.id))

    db.expire_all()
    assert sub.is_active is False


def test_push_network_error_keeps_subscription(db, user_factory, monkeypatch):
    sub, n = _push_subscription(db, user_factory)
    monkeypatch.setattr(
        notify_module, "webpush_async", AsyncMock(side_effect=asyncio.TimeoutError()),
    )
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "key")

    asyncio.run(notify_module.notify(n.id))

    db.expire_all()
    assert sub.is_active is True
