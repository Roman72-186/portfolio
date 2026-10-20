"""Тесты ленты уведомлений для попапа колокольчика.

Эндпоинты:
  GET  /cabinet/notifications/feed       — JSON-лента (read-only)
  POST /cabinet/notifications/mark-read  — отметить прочитанными (JSON-ответ)
"""


def test_notifications_feed_returns_json(auth_client, db):
    from app.models.notification import Notification

    client, user = auth_client
    db.add(Notification(user_id=user.id, title="Тест", text="текст", is_read=False))
    db.add(Notification(user_id=user.id, title="Старое", text=None, is_read=True))
    db.commit()

    r = client.get("/cabinet/notifications/feed")
    assert r.status_code == 200
    body = r.json()
    assert body["unread_count"] == 1
    titles = [n["title"] for n in body["notifications"]]
    assert "Тест" in titles and "Старое" in titles
    # feed только читает — непрочитанное остаётся непрочитанным
    assert db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read.is_(False)
    ).count() == 1


def test_mark_read_returns_json_and_clears(auth_client, db):
    from app.models.notification import Notification

    client, user = auth_client
    db.add(Notification(user_id=user.id, title="Новое", is_read=False))
    db.commit()

    r = client.post("/cabinet/notifications/mark-read")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert db.query(Notification).filter(
        Notification.user_id == user.id, Notification.is_read.is_(False)
    ).count() == 0


def test_feed_only_own_notifications(auth_client, db):
    from app.models.notification import Notification

    client, user = auth_client
    db.add(Notification(user_id=user.id, title="Моё", is_read=False))
    db.add(Notification(user_id=user.id + 999, title="Чужое", is_read=False))
    db.commit()

    body = client.get("/cabinet/notifications/feed").json()
    titles = [n["title"] for n in body["notifications"]]
    assert "Моё" in titles
    assert "Чужое" not in titles


def test_notification_time_shown_in_moscow(auth_client, db):
    """В базе время в UTC, человеку — по Москве. До 08.10.2026 колокольчик и
    страница уведомлений печатали UTC как есть и отставали на три часа."""
    from datetime import datetime, timezone

    from app.models.notification import Notification

    client, user = auth_client
    db.add(Notification(
        user_id=user.id, title="Время", is_read=False,
        created_at=datetime(2026, 10, 8, 7, 5, tzinfo=timezone.utc),
    ))
    db.commit()

    body = client.get("/cabinet/notifications/feed").json()
    item = next(n for n in body["notifications"] if n["title"] == "Время")
    assert item["created_at"] == "08.10.2026 в 10:05"

    assert client.get("/cabinet/notifications").status_code == 200
