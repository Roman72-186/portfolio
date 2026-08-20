"""Тесты тумблера Telegram-уведомлений (шестерёнка рядом с "Выйти").

Эндпоинт: POST /cabinet/notifications/telegram-toggle
"""


def test_toggle_off_persists(auth_client, db):
    client, user = auth_client
    user.telegram_chat_id = 555_555
    db.commit()

    r = client.post("/cabinet/notifications/telegram-toggle", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    db.refresh(user)
    assert user.telegram_notifications_enabled is False


def test_toggle_on_persists(auth_client, db):
    client, user = auth_client
    user.telegram_chat_id = 555_555
    user.telegram_notifications_enabled = False
    db.commit()

    r = client.post("/cabinet/notifications/telegram-toggle", json={"enabled": True})
    assert r.status_code == 200

    db.refresh(user)
    assert user.telegram_notifications_enabled is True


def test_toggle_without_linked_telegram_returns_400(auth_client, db):
    client, user = auth_client
    assert user.telegram_chat_id is None

    r = client.post("/cabinet/notifications/telegram-toggle", json={"enabled": False})
    assert r.status_code == 400

    db.refresh(user)
    assert user.telegram_notifications_enabled is True
