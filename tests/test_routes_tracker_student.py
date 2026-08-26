"""Экран ученика «Личный трекер» (/cabinet/tracker): неделя с днями и заданиями."""

from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.tag import Tag, UserTag
from app.models.tracker import STATUS_DONE, STATUS_OPEN, TrackerTask, TrackerTaskState
from app.services.program import day_bounds, week_start
from app.services.tracker import create_task, set_task_assignees, set_task_tags, task_status
from app.services.tz import msk_midnight, today_msk

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

def test_toggle_creates_state_lazily_and_is_one_way(auth_client, db):
    """Отметка выполнения необратима (решение владельца 26.08.2026): повторный
    вызов на уже закрытой задаче — no-op, а не откат в открытое состояние."""
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
    completed_at = state.completed_at

    resp = client.post(f"{PAGE}/tasks/{task.id}/toggle")
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    db.refresh(state)
    assert state.status == STATUS_DONE
    assert state.completed_by_id == user.id
    assert state.completed_at == completed_at


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


# ── Закрытый долг не исчезает с экрана (21.08) ──────────────────────────────

def test_closed_overdue_task_stays_visible_in_done(auth_client, db):
    """Долг прошлой недели, закрытый сегодня, остаётся в блоке «Сделано».

    Раньше «Сделано» отбиралось по дате дедлайна, и такая задача пропадала
    совсем: статус вывел её из «Просрочено», а старый дедлайн не пустил в
    «Сделано». Ученик закрывал долг и после обновления страницы не находил
    ни задачи, ни подтверждения.
    """
    client, user = auth_client
    last_week = week_start(today_msk()) - timedelta(days=3)
    start, _ = day_bounds(last_week)
    task = _standalone_task(
        db, user_id=user.id, due_at=start + timedelta(hours=10), assign_to_all=True
    )

    resp = client.get(PAGE)
    assert "Разовая задача" in resp.text  # видна как просроченная

    assert client.post(f"{PAGE}/tasks/{task.id}/toggle").json()["status"] == "done"

    resp = client.get(PAGE)
    assert "Разовая задача" in resp.text
    assert "Сделано на этой неделе" in resp.text


def test_task_closed_long_ago_is_not_shown(auth_client, db):
    """Отбор по дате закрытия не должен превратиться в «показывать всё».

    Задача прошлой недели, закрытая тогда же, на экране этой недели не нужна.
    """
    client, user = auth_client
    last_week = week_start(today_msk()) - timedelta(days=3)
    start, _ = day_bounds(last_week)
    task = _standalone_task(
        db, user_id=user.id, due_at=start + timedelta(hours=10), assign_to_all=True
    )
    db.add(TrackerTaskState(
        task_id=task.id,
        user_id=user.id,
        status=STATUS_DONE,
        completed_at=start + timedelta(hours=12),
        completed_by_id=user.id,
    ))
    db.commit()

    resp = client.get(PAGE)
    assert "Разовая задача" not in resp.text


# ── Умные кнопки вместо голой галочки (21.08) ───────────────────────────────

def _video_task_with_video(db, user_id, *, due_at):
    """Задача-видео с настоящим роликом — как её заводит учебная программа."""
    from app.models.learning_video import LearningVideo
    from app.models.tracker import ITEM_VIDEO
    from app.services.program import ensure_item_topic, set_item_audience

    topic = ensure_item_topic(db, title="Видео недели", day=today_msk(), user_id=user_id)
    set_item_audience(db, topic, assign_to_all=True, tag_ids=[], assignee_ids=[])
    task = create_task(
        db, title="Видео недели", user_id=user_id, due_at=due_at,
        topic_id=topic.id, kind=ITEM_VIDEO,
    )
    task.is_published = True
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id="35ed80ae-8103-4528-a700-3f69ec56957d",
        title="Видео недели",
        topic_id=topic.id,
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return task, video


def test_video_task_links_to_player_instead_of_checkbox(auth_client, db):
    """Видео на трекере ведёт в плеер, а не закрывается галочкой.

    Иначе ученик отмечает урок просмотренным, не открыв его — а если на такие
    отметки повесят доступ к следующей неделе (Р1), это обход в одно нажатие.
    """
    client, user = auth_client
    _, due = _current_week_due(offset_days=1)
    task, video = _video_task_with_video(db, user.id, due_at=due)

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert f'href="/cabinet/videos/{video.id}"' in resp.text
    assert f'data-toggle-task="{task.id}"' not in resp.text


def test_task_without_own_screen_keeps_checkbox(auth_client, db):
    """У задачи без своего экрана галочка остаётся — она единственный способ закрыть."""
    client, user = auth_client
    _, due = _current_week_due(offset_days=1)
    task = _standalone_task(db, user_id=user.id, due_at=due, assign_to_all=True)

    resp = client.get(PAGE)
    assert f'data-toggle-task="{task.id}"' in resp.text


def test_mock_exam_task_has_no_manual_checkbox(auth_client, db):
    """У пробника нет ручной отметки — только переход, задачу закрывает сдача.

    Решение владельца 23.08 (гейт «блок → неделя → месяц»): раньше отметку
    держали, потому что `close_task_for_user` для пробника нигде не вызывался
    и задача не смогла бы уйти из «Просрочено». Теперь её закрывает
    `close_cycle`/`close_cycle_auto` — ручная кнопка осталась бы обходом
    гейта в одно нажатие.
    """
    from app.models.tracker import ITEM_MOCK_EXAM

    client, user = auth_client
    _, due = _current_week_due(offset_days=1)
    task = create_task(
        db, title="Пробник по рисунку", user_id=user.id, due_at=due,
        assign_to_all=True, kind=ITEM_MOCK_EXAM, subject="Рисунок",
    )
    task.is_published = True
    db.commit()

    resp = client.get(PAGE)
    assert "Начать пробник" in resp.text
    assert f'data-toggle-task="{task.id}"' not in resp.text


def test_student_cannot_toggle_mock_exam_or_homework_task(auth_client, db):
    """Регрессия дыры: прямой POST /toggle на homework/mock_exam запрещён,
    даже если кнопки в интерфейсе уже нет — раньше это закрывало задачу в
    обход факта сдачи."""
    from app.models.tracker import ITEM_HOMEWORK, ITEM_MOCK_EXAM

    client, user = auth_client
    _, due = _current_week_due(offset_days=1)
    mock_task = create_task(
        db, title="Пробник по рисунку", user_id=user.id, due_at=due,
        assign_to_all=True, kind=ITEM_MOCK_EXAM, subject="Рисунок",
    )
    mock_task.is_published = True
    homework_task = create_task(
        db, title="Самостоятельная работа", user_id=user.id, due_at=due,
        assign_to_all=True, kind=ITEM_HOMEWORK,
    )
    homework_task.is_published = True
    db.commit()

    for task in (mock_task, homework_task):
        resp = client.post(f"{PAGE}/tasks/{task.id}/toggle")
        assert resp.status_code == 403


def test_tracker_shows_behind_schedule_warning_for_debtor(auth_client, db):
    """Гейт «блок → неделя → месяц» (23.08): застрявший ученик видит красное
    предупреждение в «Личном трекере» (АОП баннеров не показывает вовсе —
    решение владельца, экран застрявшего ученика)."""
    client, user = auth_client
    monday = week_start(today_msk())
    last_monday = monday - timedelta(days=7)
    user.created_at = msk_midnight(last_monday - timedelta(days=30))
    db.commit()

    db.add(LearningTopic(
        title="Неделя с долгом", opens_at=msk_midnight(last_monday),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=user.id,
    ))
    db.commit()
    due = day_bounds(last_monday)[0] + timedelta(hours=10)
    task = create_task(
        db, title="Долг прошлой недели", user_id=user.id, due_at=due,
        assign_to_all=True, kind="homework",
    )
    task.is_published = True
    db.commit()

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Ты отстаёшь от текущей программы" in resp.text


def test_tracker_hides_behind_schedule_warning_without_debt(auth_client):
    client, _ = auth_client
    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Ты отстаёшь от текущей программы" not in resp.text
