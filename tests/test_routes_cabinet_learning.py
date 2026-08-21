"""Tests for GET /cabinet/learning — «Актуальное образовательное пространство» (трек A)."""
from datetime import timedelta

from app.models.learning_topic import LearningTopic
from app.services.program import day_bounds, week_start
from app.services.tracker import create_task
from app.services.tz import now_msk, today_msk


def _topic(db, owner, *, assign_to_all=True, opens_in_days=-1, is_published=True,
           title="Неделя 1", meeting_url=None):
    topic = LearningTopic(
        title=title,
        opens_at=now_msk() + timedelta(days=opens_in_days),
        assign_to_all=assign_to_all,
        is_published=is_published,
        created_by_id=owner.id,
        meeting_url=meeting_url,
    )
    db.add(topic)
    db.commit()
    return topic


def test_learning_without_auth_redirects(client):
    resp = client.get("/cabinet/learning", follow_redirects=False)
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_learning_redirects_to_profile_when_incomplete(client, user_factory, session_factory):
    user = user_factory(profile_completed=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/learning", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


def test_learning_shows_empty_state_without_accessible_topics(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Пока нет ни одной доступной вам недели" in resp.text


def test_learning_shows_current_topic_title(auth_client, db):
    client, user = auth_client
    _topic(db, user, title="Неделя про композицию")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Неделя про композицию" in resp.text


def test_learning_meeting_url_renders_join_link(auth_client, db):
    client, user = auth_client
    _topic(db, user, meeting_url="https://meet.example.com/week1")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'href="https://meet.example.com/week1"' in resp.text
    assert "Присоединиться" in resp.text


def test_learning_without_meeting_url_shows_placeholder(auth_client, db):
    client, user = auth_client
    _topic(db, user, meeting_url=None)

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Ссылка на созвон появится позже" in resp.text


def test_learning_bottom_nav_highlights_learning_tab(auth_client, db):
    client, user = auth_client
    _topic(db, user)

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'href="/cabinet/learning"' in resp.text
    assert 'class="bottom-nav"' in resp.text


# ── Полоска недели и разбивка по дням (21.08) ───────────────────────────────

def test_learning_shows_current_week_task(auth_client, db):
    client, user = auth_client
    day = week_start(today_msk()) + timedelta(days=2)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(db, title="Сдать эскиз", user_id=user.id, due_at=due, assign_to_all=True)
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Сдать эскиз" in resp.text
    assert 'class="trk-weekstrip"' in resp.text


def test_learning_task_outside_current_week_not_shown(auth_client, db):
    client, user = auth_client
    day = week_start(today_msk()) + timedelta(days=14)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(db, title="Задача через две недели", user_id=user.id, due_at=due, assign_to_all=True)
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Задача через две недели" not in resp.text
