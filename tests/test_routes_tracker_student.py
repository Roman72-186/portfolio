"""Экран ученика «Личный трекер» (/cabinet/tracker): неделя с днями и заданиями."""

from datetime import timedelta

from app.models.tag import Tag, UserTag
from app.models.tracker import STATUS_DONE, STATUS_OPEN, TrackerTask, TrackerTaskState
from app.services.program import day_bounds, week_start
from app.services.tracker import create_task, set_task_assignees, set_task_tags, task_status
from app.services.tz import today_msk

PAGE = "/cabinet/tracker"


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _tagged_student(db, user_factory, tag: Tag, *, vk_id: int):
    student = user_factory(vk_id=vk_id, name=f"Ученик {vk_id}", role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    return student


def _current_week_due(offset_days: int = 0, hour: int = 10):
    """Момент внутри текущей МСК-недели (UTC-границы дня, как в program.py)."""
    day = week_start(today_msk()) + timedelta(days=offset_days)
    start, _ = day_bounds(day)
    return day, start + timedelta(hours=hour)


def _standalone_task(
    db, *, user_id, due_at, assign_to_all=False, tag_ids=None, assignee_ids=None, published=True,
):
    task = create_task(
        db,
        title="Разовая задача",
        user_id=user_id,
        due_at=due_at,
        assign_to_all=assign_to_all,
    )
    if tag_ids:
        set_task_tags(db, task, tag_ids)
    if assignee_ids:
        set_task_assignees(db, task, assignee_ids)
    task.is_published = published
    db.commit()
    db.refresh(task)
    return task


def _program_item(db, *, day, due_at, tag_ids, user_id):
    from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM
    from app.services.program import ensure_item_topic, set_item_audience
    from app.models.tracker import ITEM_VIDEO

    topic = ensure_item_topic(db, title="Видео недели", day=day, user_id=user_id)
    set_item_audience(db, topic, assign_to_all=False, tag_ids=tag_ids, assignee_ids=[])
    task = create_task(
        db,
        title="Видео недели",
        user_id=user_id,
        due_at=due_at,
        topic_id=topic.id,
        kind=ITEM_VIDEO,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    assert topic.kind == TOPIC_KIND_PROGRAM_ITEM
    return task


# ── Доступ ────────────────────────────────────────────────────────────────

def test_tracker_without_auth_redirects(client):
    resp = client.get(PAGE, follow_redirects=False)
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_tracker_redirects_incomplete_profile(client, user_factory, session_factory):
    student = user_factory(vk_id=420_001, role_name="ученик", profile_completed=False)
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    resp = client.get(PAGE, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


# ── Видимость элементов недели ──────────────────────────────────────────────

def test_program_item_visible_with_matching_tag(auth_client, db):
    client, user = auth_client
    tag = _tag(db, "Поток 1")
    db.add(UserTag(user_id=user.id, tag_id=tag.id))
    db.commit()
    day, due = _current_week_due(offset_days=1)
    _program_item(db, day=day, due_at=due, tag_ids=[tag.id], user_id=user.id)

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Видео недели" in resp.text


def test_program_item_hidden_without_matching_tag(auth_client, db):
    client, user = auth_client
    tag = _tag(db, "Поток 1")
    day, due = _current_week_due(offset_days=1)
    _program_item(db, day=day, due_at=due, tag_ids=[tag.id], user_id=user.id)

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Видео недели" not in resp.text


def test_standalone_task_visible_for_assign_to_all(auth_client, db):
    client, user = auth_client
    _, due = _current_week_due(offset_days=2)
    _standalone_task(db, user_id=user.id, due_at=due, assign_to_all=True)

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Разовая задача" in resp.text


def test_standalone_task_hidden_without_addressing(auth_client, db):
    client, user = auth_client
    _, due = _current_week_due(offset_days=2)
    _standalone_task(db, user_id=user.id, due_at=due, assign_to_all=False)

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Разовая задача" not in resp.text


def test_standalone_task_visible_by_assignee(auth_client, db):
    client, user = auth_client
    _, due = _current_week_due(offset_days=3)
    _standalone_task(db, user_id=user.id, due_at=due, assignee_ids=[user.id])

    resp = client.get(PAGE)
    assert "Разовая задача" in resp.text


def test_unpublished_task_is_hidden(auth_client, db):
    client, user = auth_client
    _, due = _current_week_due(offset_days=2)
    _standalone_task(db, user_id=user.id, due_at=due, assign_to_all=True, published=False)

    resp = client.get(PAGE)
    assert "Разовая задача" not in resp.text


def test_task_outside_current_week_not_shown(auth_client, db):
    client, user = auth_client
    day = week_start(today_msk()) + timedelta(days=14)
    start, _ = day_bounds(day)
    _standalone_task(db, user_id=user.id, due_at=start + timedelta(hours=10), assign_to_all=True)

    resp = client.get(PAGE)
    assert "Разовая задача" not in resp.text


# ── Отметка выполнения ──────────────────────────────────────────────────────

def test_toggle_creates_state_lazily_and_flips(auth_client, db):
    client, user = auth_client
    _, due = _current_week_due(offset_days=2)
    task = _standalone_task(db, user_id=user.id, due_at=due, assign_to_all=True)

    assert db.query(TrackerTaskState).count() == 0

    resp = client.post(f"{PAGE}/tasks/{task.id}/toggle")
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    state = db.query(TrackerTaskState).filter(TrackerTaskState.task_id == task.id).one()
    assert state.status == STATUS_DONE
    assert state.completed_by_id == user.id

    resp = client.post(f"{PAGE}/tasks/{task.id}/toggle")
    assert resp.status_code == 200
    assert resp.json()["status"] != "done"
    db.refresh(state)
    assert state.status == STATUS_OPEN
    assert state.completed_by_id is None


def test_toggle_state_is_per_student(auth_client, db, user_factory, session_factory):
    client, user = auth_client
    _, due = _current_week_due(offset_days=2)
    task = _standalone_task(db, user_id=user.id, due_at=due, assign_to_all=True)
    client.post(f"{PAGE}/tasks/{task.id}/toggle")

    other = user_factory(vk_id=420_099, role_name="ученик")
    other_session = session_factory(other)
    client.cookies.set("session_id", other_session.id)

    resp = client.get(PAGE)
    assert "Разовая задача" in resp.text
    states = db.query(TrackerTaskState).filter(TrackerTaskState.task_id == task.id).all()
    assert len(states) == 1
    assert states[0].user_id == user.id


def test_toggle_on_inaccessible_task_is_404(auth_client, db):
    client, user = auth_client
    _, due = _current_week_due(offset_days=2)
    task = _standalone_task(db, user_id=user.id, due_at=due, assign_to_all=False)

    resp = client.post(f"{PAGE}/tasks/{task.id}/toggle")
    assert resp.status_code == 404


def test_toggle_on_unknown_task_is_404(auth_client):
    client, _ = auth_client
    resp = client.post(f"{PAGE}/tasks/999999/toggle")
    assert resp.status_code == 404


# ── task_status(): чистая функция ───────────────────────────────────────────

def test_task_status_pure_function():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    task = TrackerTask(title="x", due_at=now + timedelta(hours=1))
    assert task_status(task, None, now=now) == "upcoming"

    overdue_task = TrackerTask(title="x", due_at=now - timedelta(hours=1))
    assert task_status(overdue_task, None, now=now) == "overdue"

    done_state = TrackerTaskState(task_id=1, user_id=1, status=STATUS_DONE)
    assert task_status(overdue_task, done_state, now=now) == "done"
