"""Тесты app/services/notify.py — учёт тумблера telegram_notifications_enabled.

Web Push и остальная логика диспетчера покрыта тестами роутов, создающих
Notification (см. session-handoffs). Здесь — только гейтинг Telegram-канала.
"""
import asyncio
from unittest.mock import AsyncMock

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
