"""Экран ученика «Личный трекер» (/cabinet/tracker): неделя с днями и заданиями."""

from datetime import timedelta, timezone

import pytest

from app.models.learning_topic import (
    TOPIC_KIND_PERIOD, TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic,
)
from app.models.tag import Tag, UserTag
from app.models.tracker import STATUS_DONE, TrackerTask, TrackerTaskState
from app.services.program import day_bounds, week_start
from app.services.tracker import (
    close_task_for_user,
    create_task,
    set_task_assignees,
    set_task_tags,
    task_status,
)
from app.services.tz import MSK_TZ, msk_midnight, today_msk

PAGE = "/cabinet/tracker"


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


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


def _program_item_tariff(db, *, day, due_at, tariffs, user_id):
    from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM
    from app.models.tracker import ITEM_VIDEO
    from app.services.program import ensure_item_topic, set_item_audience
    from app.services.video_topics import set_topic_tariffs

    topic = ensure_item_topic(db, title="Видео недели (тариф)", day=day, user_id=user_id)
    set_item_audience(db, topic, assign_to_all=True, tag_ids=[], assignee_ids=[])
    set_topic_tariffs(db, topic, tariff_restricted=True, tariffs=tariffs)
    task = create_task(
        db, title="Видео недели (тариф)", user_id=user_id, due_at=due_at,
        topic_id=topic.id, kind=ITEM_VIDEO,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    assert topic.kind == TOPIC_KIND_PROGRAM_ITEM
    return task


def test_program_item_visible_with_matching_tariff(auth_client, db):
    """Владелец 26.08.2026: тарифная видимость — то же место в accessible_topic_ids,
    что и теги, но по User.tariff вместо UserTag."""
    client, user = auth_client
    assert user.tariff == "УВЕРЕННЫЙ"
    day, due = _current_week_due(offset_days=1)
    _program_item_tariff(db, day=day, due_at=due, tariffs=["УВЕРЕННЫЙ"], user_id=user.id)

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Видео недели (тариф)" in resp.text


def test_program_item_hidden_with_mismatched_tariff(auth_client, db):
    client, user = auth_client
    assert user.tariff == "УВЕРЕННЫЙ"
    day, due = _current_week_due(offset_days=1)
    _program_item_tariff(db, day=day, due_at=due, tariffs=["МАКСИМУМ"], user_id=user.id)

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Видео недели (тариф)" not in resp.text


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


def test_undated_cycle_task_is_visible_in_personal_tracker(auth_client, db):
    """Задание внутри цикла (владелец 10.09.2026) заводится без даты —
    `accessible_task_entries` должна находить его через `topic_id`, а не
    через окно `due_at`, и шаблон не должен падать на пустой дате."""
    client, user = auth_client
    today = today_msk()
    topic = LearningTopic(
        title="Цикл",
        opens_at=msk_midnight(today - timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None),
        ends_at=msk_midnight(today + timedelta(days=5)).astimezone(timezone.utc).replace(tzinfo=None),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=user.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    task = create_task(
        db, title="Задание цикла без даты", user_id=user.id, kind="material",
        due_at=None, topic_id=topic.id, assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()

    resp = client.get(PAGE)
    assert resp.status_code == 200
    assert "Задание цикла без даты" in resp.text


# ── Срок цикла у задания без даты (владелец 06.10.2026, после аудита) ───────


def _tracker(client, rendered):
    """Трекер, разложенный роутом: «Просрочено», «В работе», «Сделано»."""
    assert client.get(PAGE).status_code == 200
    return rendered("cabinet_tracker.html")


def _titles(entries):
    return [entry["task"].title for entry in entries]


def _shown(page):
    return _titles(page["overdue"] + page["upcoming"] + page["done"])


def _msk(moment):
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MSK_TZ)


def _frame(db, owner, *, title, starts, ends, kind=TOPIC_KIND_WEEK, parent=None):
    topic = LearningTopic(
        title=title,
        opens_at=msk_midnight(starts).astimezone(timezone.utc).replace(tzinfo=None),
        ends_at=(msk_midnight(ends) + timedelta(hours=23, minutes=59))
        .astimezone(timezone.utc).replace(tzinfo=None),
        assign_to_all=True, is_published=True, kind=kind,
        created_by_id=owner.id,
        parent_id=parent.id if parent is not None else None,
    )
    db.add(topic)
    db.commit()
    return topic


def _cycle_with_task(db, owner, *, title, starts, ends, stage=None):
    topic = _frame(db, owner, title=title, starts=starts, ends=ends, parent=stage)
    task = create_task(
        db, title=f"Задание: {title}", user_id=owner.id, kind="material",
        due_at=None, topic_id=topic.id, assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    return topic, task


def test_undated_task_is_overdue_after_tariff_cycle_deadline(auth_client, db, rendered):
    """Срок «по» тарифа ученика прошёл вчера — задание в «Просрочено» с этим
    сроком, а не «В работе · без даты». Цикл при этом ещё идёт по общим датам."""
    from app.services.video_topics import set_topic_tariff_windows

    client, user = auth_client
    user.tariff = "Я САМ"
    db.commit()
    today = today_msk()
    yesterday = today - timedelta(days=1)
    topic, _ = _cycle_with_task(
        db, user, title="Цикл с тарифом", starts=today - timedelta(days=5),
        ends=today + timedelta(days=5),
    )
    set_topic_tariff_windows(db, topic, {
        "Я САМ": (
            None,
            (msk_midnight(yesterday) + timedelta(hours=20)).astimezone(timezone.utc).replace(tzinfo=None),
        ),
    })
    db.commit()

    page = _tracker(client, rendered)

    [entry] = page["overdue"]
    assert entry["task"].title == "Задание: Цикл с тарифом"
    assert _msk(entry["cycle_deadline"]) == msk_midnight(yesterday) + timedelta(hours=20)
    assert page["upcoming"] == []


def test_undated_task_shows_cycle_end_while_time_remains(auth_client, db, rendered):
    """Без «по» у тарифа срок — конец цикла: полночь после последнего дня
    (на экране — «23:59» последнего дня, как в шапке цикла)."""
    client, user = auth_client
    today = today_msk()
    last_day = today + timedelta(days=4)
    _cycle_with_task(db, user, title="Идущий цикл", starts=today - timedelta(days=2), ends=last_day)

    page = _tracker(client, rendered)

    [entry] = page["upcoming"]
    assert entry["task"].title == "Задание: Идущий цикл"
    assert _msk(entry["cycle_deadline"]) == msk_midnight(last_day + timedelta(days=1))
    assert page["overdue"] == []


def _period_stage(db, owner, *, title, starts, ends):
    """Период с одним этапом на те же даты — рамка трекера."""
    period = _frame(db, owner, title=title, starts=starts, ends=ends, kind=TOPIC_KIND_PERIOD)
    stage = _frame(
        db, owner, title=f"Этап: {title}", starts=starts, ends=ends,
        kind=TOPIC_KIND_STAGE, parent=period,
    )
    return period, stage


def test_upcoming_is_sorted_by_nearest_deadline(auth_client, db, rendered):
    client, user = auth_client
    today = today_msk()
    _, stage = _period_stage(
        db, user, title="Семестр", starts=today - timedelta(days=5), ends=today + timedelta(days=30),
    )
    far, _ = _cycle_with_task(db, user, title="Дальний", starts=today - timedelta(days=1), ends=today + timedelta(days=9), stage=stage)
    _cycle_with_task(db, user, title="Ближний", starts=today - timedelta(days=1), ends=today + timedelta(days=2), stage=stage)
    # Оба цикла открыты: первый не запирает следующий долгом.
    far.locks_next = False
    db.commit()

    assert _titles(_tracker(client, rendered)["upcoming"]) == ["Задание: Ближний", "Задание: Дальний"]


def test_closed_task_of_past_cycle_is_not_overdue(auth_client, db, rendered):
    client, user = auth_client
    today = today_msk()
    _, task = _cycle_with_task(
        db, user, title="Прошедший", starts=today - timedelta(days=9), ends=today - timedelta(days=3),
    )
    close_task_for_user(db, task, user.id, source="test")
    db.commit()

    page = _tracker(client, rendered)

    assert page["overdue"] == []
    assert _titles(page["done"]) == ["Задание: Прошедший"]


# ── Только текущий период (владелец 06.10.2026) ─────────────────────────────


def _two_periods(db, owner):
    """Прошлый период с закрытым циклом и текущий с идущим; будущий цикл
    текущего периода ещё не начался."""
    today = today_msk()
    _, old_stage = _period_stage(
        db, owner, title="Предобучение", starts=today - timedelta(days=30), ends=today - timedelta(days=3),
    )
    _, stage = _period_stage(
        db, owner, title="Семестр", starts=today - timedelta(days=2), ends=today + timedelta(days=60),
    )
    _, old_task = _cycle_with_task(
        db, owner, title="Старый цикл", starts=today - timedelta(days=20),
        ends=today - timedelta(days=5), stage=old_stage,
    )
    _cycle_with_task(
        db, owner, title="Текущий цикл", starts=today - timedelta(days=2),
        ends=today + timedelta(days=5), stage=stage,
    )
    return old_task


def test_tracker_keeps_past_period_debt(auth_client, db, rendered):
    """Ученик ушёл в новый период, а необязательное задание прошлого периода
    не сделано — оно в трекере (владелец 06.10.2026: «показываем все
    долги???»): прошлое и текущее видны, будущее — нет."""
    client, user = auth_client
    old_task = _two_periods(db, user)
    old_task.is_required = False
    db.commit()

    page = _tracker(client, rendered)

    assert _titles(page["upcoming"]) == ["Задание: Текущий цикл"]
    assert _titles(page["overdue"]) == ["Задание: Старый цикл"]


def test_debtor_does_not_see_future_period(auth_client, db, rendered):
    """Должник стоит в ленте на цикле долга в прошлом периоде — следующий
    период для него будущий и запертый, трекер его не показывает."""
    client, user = auth_client
    _two_periods(db, user)

    page = _tracker(client, rendered)

    assert _titles(page["overdue"]) == ["Задание: Старый цикл"]
    assert "Задание: Текущий цикл" not in _shown(page)


# ── Только открытое ученику (владелец 06.10.2026) ───────────────────────────


def test_tracker_shows_cycle_locked_by_debt(auth_client, db, rendered):
    """Должник цикла 1: начавшийся цикл 2 того же периода заперт долгом, но
    его задания — тоже долг, трекер показывает оба (владелец 06.10.2026:
    «нам нужно показывать долги и что нужно сделать»). Долг текущей ленты
    досдаётся кнопкой, у запертого цикла кнопки нет — подпись, какой цикл
    закрыть первым."""
    client, user = auth_client
    today = today_msk()
    _, stage = _period_stage(
        db, user, title="Семестр", starts=today - timedelta(days=20), ends=today + timedelta(days=30),
    )
    _, debt_task = _cycle_with_task(
        db, user, title="Цикл долга", starts=today - timedelta(days=15),
        ends=today - timedelta(days=8), stage=stage,
    )
    _, locked_task = _cycle_with_task(
        db, user, title="Запертый цикл", starts=today - timedelta(days=7),
        ends=today + timedelta(days=5), stage=stage,
    )

    page = _tracker(client, rendered)

    assert _titles(page["overdue"]) == ["Задание: Цикл долга"]
    assert _titles(page["upcoming"]) == ["Задание: Запертый цикл"]
    assert page["go_urls"][debt_task.id].startswith("/cabinet/learning?task=")
    assert locked_task.id in page["locked_task_ids"]
    assert locked_task.id not in page["go_urls"]
    assert page["debt_cycle_label"] == "Цикл долга"


def test_tracker_hides_not_started_cycle_and_task_opening_later(auth_client, db, rendered):
    from datetime import datetime

    client, user = auth_client
    today = today_msk()
    _, stage = _period_stage(
        db, user, title="Семестр", starts=today - timedelta(days=5), ends=today + timedelta(days=30),
    )
    _, current = _cycle_with_task(
        db, user, title="Идёт", starts=today - timedelta(days=2), ends=today + timedelta(days=5),
        stage=stage,
    )
    close_task_for_user(db, current, user.id, source="test")
    _cycle_with_task(
        db, user, title="Будущий", starts=today + timedelta(days=6), ends=today + timedelta(days=12),
        stage=stage,
    )
    _, later = _cycle_with_task(
        db, user, title="Откроется завтра", starts=today - timedelta(days=2),
        ends=today + timedelta(days=5), stage=stage,
    )
    later.starts_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    db.commit()

    assert _shown(_tracker(client, rendered)) == ["Задание: Идёт"]


def test_debt_of_past_open_cycle_links_to_its_feed(auth_client, db, rendered):
    """Необязательное задание прошлого цикла идущего этапа не сделано — в
    трекере кнопка ведёт в ленту этого цикла (`?cycle=`), чтобы досдать."""
    client, user = auth_client
    today = today_msk()
    _, stage = _period_stage(
        db, user, title="Предобучение", starts=today - timedelta(days=20), ends=today + timedelta(days=20),
    )
    past, past_task = _cycle_with_task(
        db, user, title="Прошлый", starts=today - timedelta(days=15), ends=today - timedelta(days=8),
        stage=stage,
    )
    past_task.is_required = False
    db.commit()
    _cycle_with_task(
        db, user, title="Текущий", starts=today - timedelta(days=7), ends=today + timedelta(days=5),
        stage=stage,
    )

    page = _tracker(client, rendered)

    assert _titles(page["overdue"]) == ["Задание: Прошлый"]
    assert page["go_urls"][past_task.id] == f"/cabinet/learning?cycle={past.id}#learning-task-{past_task.id}"


def test_tracker_row_shows_task_type_by_content(auth_client, db, rendered):
    """Тип задания — по блокам (владелец 06.10.2026, «по содержимому»):
    работа на время главнее видео; блок чужого тарифа тип не задаёт."""
    from app.models.task_block import TaskBlock, TaskBlockTariff

    client, user = auth_client
    user.tariff = "Я САМ"
    db.commit()
    today = today_msk()
    _, stage = _period_stage(
        db, user, title="Семестр", starts=today - timedelta(days=5), ends=today + timedelta(days=30),
    )
    cycle, exam = _cycle_with_task(
        db, user, title="Цикл", starts=today - timedelta(days=2), ends=today + timedelta(days=5), stage=stage,
    )
    exam.title = "Контрольная"
    video_task = create_task(
        db, title="Лекция", user_id=user.id, kind="material", due_at=None,
        topic_id=cycle.id, assign_to_all=True, is_required=False,
    )
    video_task.is_published = True
    db.add_all([
        TaskBlock(task_id=exam.id, block_type="video", sort_order=0),
        TaskBlock(task_id=exam.id, block_type="timed", sort_order=1),
        TaskBlock(task_id=video_task.id, block_type="video", sort_order=0),
    ])
    foreign = TaskBlock(task_id=video_task.id, block_type="upload", sort_order=1)
    db.add(foreign)
    db.flush()
    db.add(TaskBlockTariff(block_id=foreign.id, tariff="Я С ВАМИ"))
    db.commit()

    labels = _tracker(client, rendered)["content_labels"]

    assert labels[exam.id] == "Контрольная на время"
    assert labels[video_task.id] == "Видео"


def test_task_status_uses_cycle_deadline_only_without_due_at():
    from datetime import datetime

    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    passed = now - timedelta(hours=1)
    undated = TrackerTask(due_at=None)
    dated = TrackerTask(due_at=now + timedelta(days=1))

    assert task_status(undated, None, now=now, cycle_deadline=passed) == "overdue"
    assert task_status(undated, None, now=now) == "upcoming"
    # Свой день у задания главнее срока цикла.
    assert task_status(dated, None, now=now, cycle_deadline=passed) == "upcoming"


# ── Шапка трекера: баллы Р/К и точка А (владелец 04.10.2026) ─────────────────


def _scored_submission(db, user_id, *, score, block_subject=None, task_subject=None, deleted=False):
    from datetime import datetime

    from app.models.task_block import TaskBlock, TaskBlockSubmission

    now = datetime.now(timezone.utc)
    task = TrackerTask(title="Контрольная", subject=task_subject,
                       deleted_at=now if deleted else None)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type="photo_upload", subject=block_subject)
    db.add(block)
    db.flush()
    db.add(TaskBlockSubmission(
        block_id=block.id, user_id=user_id, submitted_at=now, score=score, scored_at=now,
    ))
    db.commit()


def test_avg_scores_include_task_block_submissions(db, user_factory):
    """С 17.07.2026 баллы ставятся сдачам в заданиях, а не работам-пробникам:
    без второго источника у всех новых оценок в шапке стоял прочерк.
    Предмет — у блока, иначе у задания; удалённое задание в среднее не идёт."""
    from app.models.work import WORK_TYPE_MOCK_EXAM, Work
    from app.services.stats import avg_score_by_subject_all_time

    student = user_factory(vk_id=980401, name="Баллы", role_name="ученик")
    db.add(Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, subject="Рисунок",
        month="июль", year=2026, filename="m.jpg", status="success", score=60,
        tariff=student.tariff,
    ))
    db.commit()
    _scored_submission(db, student.id, score=80, block_subject="Рисунок")
    _scored_submission(db, student.id, score=55, task_subject="Композиция")
    _scored_submission(db, student.id, score=10, task_subject="Композиция", deleted=True)
    _scored_submission(db, student.id, score=99)  # без предмета

    assert avg_score_by_subject_all_time(db, student.id) == {"Рисунок": 70, "Композиция": 55}


def test_tracker_hero_shows_task_block_score(auth_client, db, rendered):
    client, user = auth_client
    _scored_submission(db, user.id, score=67, block_subject="Композиция")

    assert _tracker(client, rendered)["avg_score_by_subject"] == {"Композиция": 67}


def _fake_point_a(monkeypatch, *, is_done, average):
    from app.api import cabinet_tracker
    from app.services.point_a import PointA

    monkeypatch.setattr(
        cabinet_tracker, "student_point_a",
        lambda db, student, with_images=True: PointA(
            student=student, plates=[], average=average, is_done=is_done, scored_count=0,
        ),
    )


def test_tracker_hero_shows_point_a_when_done(auth_client, monkeypatch, rendered):
    client, _ = auth_client
    _fake_point_a(monkeypatch, is_done=True, average=74)

    assert _tracker(client, rendered)["point_a_average"] == 74


@pytest.mark.parametrize("average,level", [(65, 1), (66, 2)])
def test_tracker_hero_shows_program_level(auth_client, monkeypatch, rendered, average, level):
    """Плашка уровня рядом с тарифом (владелец 05.10.2026): ≤65 — уровень 1,
    ≥66 — уровень 2."""
    client, _ = auth_client
    _fake_point_a(monkeypatch, is_done=True, average=average)

    assert _tracker(client, rendered)["point_a_level"] == level


def test_tracker_hero_hides_partial_point_a(auth_client, monkeypatch, rendered):
    """Пока ГП не оценил все плашки, среднее неполное — его не показываем:
    уровень ученик узнаёт уведомлением ровно в момент разбора."""
    client, _ = auth_client
    _fake_point_a(monkeypatch, is_done=False, average=74)

    page = _tracker(client, rendered)
    assert page["point_a_average"] is None
    assert page["point_a_level"] is None
