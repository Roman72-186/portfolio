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


# ── Выбор актуальной недели (21.08) ─────────────────────────────────────────

def test_learning_shows_latest_opened_week_not_the_first(auth_client, db):
    """Открыты две недели — в шапке должна быть поздняя.

    Раньше выборка шла по возрастанию `opens_at`, а у `accessible_topic_ids`
    нет верхней границы окна: экран навсегда застревал на первой неделе курса
    и вместе с ней отдавал её ссылку на созвон.
    """
    client, user = auth_client
    _topic(db, user, title="Неделя 1", opens_in_days=-14,
           meeting_url="https://meet.example.com/week1")
    _topic(db, user, title="Неделя 3", opens_in_days=-1,
           meeting_url="https://meet.example.com/week3")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Неделя 3" in resp.text
    assert "Неделя 1" not in resp.text
    assert 'href="https://meet.example.com/week3"' in resp.text
    assert "week1" not in resp.text


def test_learning_ignores_program_item_topics(auth_client, db):
    """Служебная тема элемента программы не может стать «актуальной неделей».

    `program.py::ensure_item_topic` заводит по теме на каждый элемент, и они
    открываются позже недели. Без фильтра по `kind` в шапку попадало название
    элемента, а `meeting_url` у служебной темы всегда пустой — ссылка на
    созвон не показывалась никогда.
    """
    from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM

    client, user = auth_client
    # Служебная тема открыта раньше недели — так бывает, когда неделю завели
    # позже её элементов. Порядок важен: иначе тест прошёл бы и на старом коде.
    item = _topic(db, user, title="Видео недели", opens_in_days=-14)
    _topic(db, user, title="Неделя 2", opens_in_days=-7,
           meeting_url="https://meet.example.com/week2")
    item.kind = TOPIC_KIND_PROGRAM_ITEM
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Неделя 2" in resp.text
    assert "Видео недели" not in resp.text
    assert 'href="https://meet.example.com/week2"' in resp.text


def test_learning_marks_task_subject_for_the_switch(auth_client, db):
    """Задачи выводятся с `data-subject` — по нему переключатель их и фильтрует.

    Сама фильтрация живёт в JS, здесь держим контракт разметки: без атрибута
    переключатель снова станет декоративным, как до 21.08.
    """
    client, user = auth_client
    _topic(db, user, title="Неделя 1")
    day = week_start(today_msk()) + timedelta(days=1)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(
        db, title="Натюрморт", user_id=user.id, due_at=due,
        assign_to_all=True, subject="Рисунок",
    )
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'data-subject="Рисунок"' in resp.text
    # Переключатель обязан стоять выше списка, который фильтрует.
    assert resp.text.index('class="lrn-subject-toggle"') < resp.text.index('class="trk-weekstrip"')
