import importlib.util
import hmac
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_link_report_and_event_retry(db, admin_client, monkeypatch, user_factory, session_factory):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/b239f86d57ca_bot_analytics.py"
    spec = importlib.util.spec_from_file_location("bot_analytics_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(db.connection()))
    migration.upgrade()
    db.commit()
    monkeypatch.setenv("FUNNEL_BOT_USERNAME", "test_funnel_bot")
    monkeypatch.setenv("FUNNEL_ANALYTICS_KEY", "test-key")
    client, _ = admin_client
    page = "/cabinet/superadmin/stats/telegram"
    for title in ("TikTok profile", "TikTok video"):
        response = client.post(page, data={
            "title": title, "utm_source": "tiktok", "utm_medium": "social", "utm_campaign": "trial",
        }, follow_redirects=False)
        assert response.status_code == 303
    first, second = db.execute(text("SELECT code FROM bot_links ORDER BY title")).scalars().all()
    for code in (first, first, second):
        response = client.get(f"/go/telegram/{code}", follow_redirects=False)
        assert response.headers["location"].endswith(f"?start={code}")

    def event(kind, value):
        return dict(uid=1234, kind=kind, value=value, event_key=uuid4().hex,
                    at=datetime.now(timezone.utc).isoformat())

    original = event("start", first)
    for payload in (original, original, event("start", second), event("button", "lesson"),
                    event("button", "lesson"), event("material", "invite")):
        response = client.post("/internal/telegram-analytics", json=payload,
                               headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 200
    summary = client.get(page, params={"export": "summary"}).text
    assert f"{first},TikTok profile,click,2" in summary
    assert f"{first},TikTok profile,button/lesson,1" in summary
    assert f"{second},TikTok video,tagged_start,1" in summary
    history = client.get(page, params={"export": "events", "code": first}).text
    assert history.count(",button,lesson,") == 2
    assert db.execute(text("SELECT COUNT(*) FROM bot_events WHERE kind='start'")).scalar() == 2
    ref = hmac.digest(b"test-key", b"funnel-payment:1234", "sha256").hex()
    payment = client.get("/go/telegram-payment", params={"ref": ref}, follow_redirects=False)
    assert payment.headers["location"] == "/testpodgotovki?utm_source=tiktok&utm_medium=social&utm_campaign=trial"
    assert client.post("/internal/telegram-analytics", json=event("step", "offer")).status_code == 401
    assert client.get(page, params={"uid": 1234}).status_code == 200
    assert client.post(f"{page}/{first}/archive", follow_redirects=False).status_code == 303
    assert client.get(f"/go/telegram/{first}", follow_redirects=False).status_code == 307

    for index, (role, can_view, can_edit) in enumerate((
        ("модератор", True, False),
        ("админ", True, True),
        ("куратор", False, False),
        ("ученик", False, False),
    )):
        user = user_factory(vk_id=840_100 + index, role_name=role)
        client.cookies.set("session_id", session_factory(user).id)
        view = client.get(page)
        assert view.status_code == (200 if can_view else 403), role
        assert client.get(page, params={"export": "events"}).status_code == (200 if can_view else 403), role
        assert client.post(f"{page}/{first}/archive", follow_redirects=False).status_code == (
            303 if can_edit else 403
        ), role
        if can_view:
            assert (f'<form method="post" action="{page}"' in view.text) == can_edit
            assert 'href="/cabinet/staff/statistics"' not in view.text
            entry = "/cabinet/admin-panel" if can_edit else "/cabinet/superadmin/activity"
            assert f'href="{page}"' in client.get(entry).text
            home = client.get("/cabinet/staff/statistics")
            assert home.status_code == 200 and f'href="{page}"' in home.text
