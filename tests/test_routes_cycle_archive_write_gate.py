"""Read-only архивного цикла — гейт на бэкенде, не только в шаблоне
(владелец 24.09.2026, Этапы).

До этой проверки read-only соблюдался только в `cabinet_learning.html`
(кнопки скрывались условием `feed.is_archive`), а пишущие эндпоинты трекера
проверяли лишь доступность задачи, не то, текущий это цикл или пройденный —
прямой POST на архивный блок технически проходил.
"""
from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()


def _utc(value):
    from datetime import timezone
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner, *, starts_on, ends_on, title):
    topic = LearningTopic(
        title=title,
        opens_at=_utc(msk_midnight(starts_on)),
        ends_at=_utc(msk_midnight(ends_on) + timedelta(hours=23, minutes=59)),
        assign_to_all=True,
        is_published=True,
        kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _task_in_topic(db, owner, topic, *, title="Задание", is_required=False):
    # `is_required=False` по умолчанию: незакрытая обязательная задача в
    # старом цикле держит `effective_cycle` на нём же («долг важнее
    # новизны», `tracker.py::effective_cycle`) — цикл тогда вовсе не архив,
    # это отдельное поведение, не то, что здесь проверяется.
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        topic_id=topic.id, assign_to_all=True, is_required=is_required,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def test_cannot_toggle_task_in_archived_cycle(auth_client, db):
    client, user = auth_client
    old_cycle = _cycle(
        db, user, title="Старый цикл",
        starts_on=TODAY - timedelta(days=20), ends_on=TODAY - timedelta(days=10),
    )
    current_cycle = _cycle(
        db, user, title="Текущий цикл",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=10),
    )
    old_task = _task_in_topic(db, user, old_cycle, title="Задание старого цикла")

    resp = client.post(f"/cabinet/tracker/tasks/{old_task.id}/toggle")

    assert resp.status_code == 403


def test_can_toggle_task_in_current_cycle(auth_client, db):
    client, user = auth_client
    _cycle(
        db, user, title="Старый цикл",
        starts_on=TODAY - timedelta(days=20), ends_on=TODAY - timedelta(days=10),
    )
    current_cycle = _cycle(
        db, user, title="Текущий цикл",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=10),
    )
    current_task = _task_in_topic(db, user, current_cycle, title="Задание текущего цикла")

    resp = client.post(f"/cabinet/tracker/tasks/{current_task.id}/toggle")

    assert resp.status_code == 200


def test_can_still_read_blocks_of_archived_task(auth_client, db):
    """GET на архивный блок остаётся 200 — только запись заперта."""
    client, user = auth_client
    old_cycle = _cycle(
        db, user, title="Старый цикл",
        starts_on=TODAY - timedelta(days=20), ends_on=TODAY - timedelta(days=10),
    )
    _cycle(
        db, user, title="Текущий цикл",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=10),
    )
    old_task = _task_in_topic(db, user, old_cycle, title="Задание старого цикла")

    resp = client.get(f"/cabinet/tracker/tasks/{old_task.id}/blocks")

    assert resp.status_code == 200


def test_running_cycle_is_not_archive_even_if_another_is_current(auth_client, db):
    """Прецедент 25.09.2026: «Цикл 1» (23–27.09) и «Цикл 2» (весь этап,
    16.09–04.10) шли одновременно. Текущим сервер выбрал второй, и все отметки
    в первом получали 403, хотя цикл ещё шёл. Архив — только закончившийся."""
    client, user = auth_client
    _cycle(
        db, user, title="Длинный цикл",
        starts_on=TODAY - timedelta(days=8), ends_on=TODAY + timedelta(days=10),
    )
    short = _cycle(
        db, user, title="Короткий цикл внутри",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=2),
    )
    other = _cycle(
        db, user, title="Ещё один идущий",
        starts_on=TODAY - timedelta(days=5), ends_on=TODAY + timedelta(days=5),
    )
    for topic in (short, other):
        task = _task_in_topic(db, user, topic, title=f"Задание {topic.title}")
        resp = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
        assert resp.status_code == 200, (topic.title, resp.text)
