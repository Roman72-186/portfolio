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


def test_bell_shows_error_then_empty_and_keeps_badge_on_failed_mark(auth_client, db):
    import pytest

    from app.models.notification import Notification

    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    client, user = auth_client
    db.add(Notification(user_id=user.id, title="Новое", is_read=False))
    db.commit()
    feed_status = 503
    feed_body = "{}"

    def serve(route):
        url = route.request.url
        if not url.startswith("https://testserver/"):
            route.abort()
        elif url.endswith("/notifications/feed"):
            route.fulfill(status=feed_status, content_type="application/json", body=feed_body)
        elif url.endswith("/notifications/mark-read"):
            route.fulfill(status=503, content_type="application/json", body="{}")
        else:
            response = client.get(url, follow_redirects=False)
            route.fulfill(
                status=response.status_code,
                headers={key: value for key, value in response.headers.items() if key != "content-length"},
                body=response.content,
            )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/*", serve)
        page.goto("https://testserver/cabinet/portfolio")
        bell = page.locator("button.notif-bell")
        body = page.locator("#notifPopBody")
        bell.click()
        body.get_by_text("Не удалось загрузить уведомления.").wait_for()
        feed_status = 200
        feed_body = '{"unread_count": 1, "notifications": [{"title":"Новое"}]}'
        with page.expect_response("**/notifications/mark-read"):
            body.get_by_role("button", name="Повторить").click()
        assert body.get_by_text("Новое").is_visible()
        assert bell.locator("[data-notif-bell-badge]").is_visible()

        feed_body = '{"unread_count": 0, "notifications": []}'
        page.locator("#notifPopClose").click()
        bell.click()
        body.get_by_text("Уведомлений пока нет").wait_for()
        assert not bell.locator("[data-notif-bell-badge]").is_visible()
        browser.close()
