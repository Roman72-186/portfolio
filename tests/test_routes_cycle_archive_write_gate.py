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


def _dated_task_in_topic(db, owner, topic, *, day, title):
    from app.services.program import day_bounds
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        topic_id=topic.id, due_at=day_bounds(day)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def test_task_shown_in_current_feed_is_writable_whatever_its_topic(auth_client, db):
    """Прецедент 28.09.2026: задание приписано к закончившемуся циклу, а датой
    стоит в текущем. Лента собирает датные задания по датам и показывает его
    в текущем цикле, а гейт архива смотрел на `topic_id` — ученик видел
    видео, жал кружок и читал «Не удалось отметить» (сервер отвечал 403
    «Цикл пройден»). Что ученик видит в текущей ленте, то он и может делать.
    """
    from app.models.task_block import BLOCK_VIDEO, TaskBlock
    from app.services.cycle_feed import current_feed_task_ids

    client, user = auth_client
    old_cycle = _cycle(
        db, user, title="Цикл 2",
        starts_on=TODAY - timedelta(days=10), ends_on=TODAY - timedelta(days=2),
    )
    _cycle(
        db, user, title="Цикл 3",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=5),
    )
    task = _dated_task_in_topic(db, user, old_cycle, day=TODAY, title="Видео урока")
    block = TaskBlock(task_id=task.id, block_type=BLOCK_VIDEO, title="Урок", sort_order=0)
    db.add(block)
    db.commit()
    assert task.id in current_feed_task_ids(db, user_id=user.id, today=TODAY)

    resp = client.post(f"/cabinet/tracker/blocks/{block.id}/watched")

    assert resp.status_code == 200, resp.text
    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 200


def test_task_of_archived_cycle_outside_current_feed_stays_locked(auth_client, db):
    """Обратная сторона: задание старого цикла, которое датой в своём же
    закончившемся цикле, по-прежнему только для чтения."""
    client, user = auth_client
    old_cycle = _cycle(
        db, user, title="Цикл 2",
        starts_on=TODAY - timedelta(days=10), ends_on=TODAY - timedelta(days=2),
    )
    _cycle(
        db, user, title="Цикл 3",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=5),
    )
    task = _dated_task_in_topic(
        db, user, old_cycle, day=TODAY - timedelta(days=5), title="Старое задание",
    )
    # Обязательное старое задание держало бы цикл 2 текущим («долг важнее
    # новизны»), здесь проверяется именно архив — закрываем его заранее.
    from app.services.tracker import close_task_for_user
    close_task_for_user(db, task, user.id, source="test")
    db.commit()

    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 403


def test_task_shown_in_a_running_chosen_cycle_is_writable(auth_client, db):
    """Прецедент 28.09.2026, второй заход: ученик с долгом стоит на раннем
    цикле (`effective_cycle` — «долг важнее новизны»), а открыл идущий цикл 3
    через карусель (`?cycle=`). Экран даёт там действовать — цикл не архив.
    Задание приписано к закончившемуся циклу 2, но датой стоит в цикле 3 и
    видно в его ленте. Первая починка сверялась только с лентой текущего
    цикла и по-прежнему отвечала 403.
    """
    from app.models.task_block import BLOCK_VIDEO, TaskBlock
    from app.services.cycle_feed import feed_for_student

    client, user = auth_client
    debt_cycle = _cycle(
        db, user, title="Цикл 1",
        starts_on=TODAY - timedelta(days=20), ends_on=TODAY - timedelta(days=12),
    )
    _dated_task_in_topic(db, user, debt_cycle, day=TODAY - timedelta(days=15), title="Долг")
    old_cycle = _cycle(
        db, user, title="Цикл 2",
        starts_on=TODAY - timedelta(days=10), ends_on=TODAY - timedelta(days=2),
    )
    running = _cycle(
        db, user, title="Цикл 3",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=5),
    )
    task = _dated_task_in_topic(db, user, old_cycle, day=TODAY, title="Видео урока")
    block = TaskBlock(task_id=task.id, block_type=BLOCK_VIDEO, title="Урок", sort_order=0)
    db.add(block)
    db.commit()
    feed = feed_for_student(
        db, user_id=user.id, user_tariff=user.tariff, today=TODAY, cycle_id=running.id,
    )
    assert not feed["is_archive"]
    assert any(step["task"].id == task.id for step in feed["steps"])

    resp = client.post(f"/cabinet/tracker/blocks/{block.id}/watched")

    assert resp.status_code == 200, resp.text


def test_check_blocks_can_be_marked_in_archived_cycle(auth_client, db):
    """Решение владельца 28.09.2026: этап закончился 27-го, а ученики досматривают
    видео. Блоки без сдачи работы (видео, фото, голосовое) отмечаются и в
    пройденном цикле — опоздание видно в статистике «до срока / после срока».
    Сдачу работ, ответы и отметку задания целиком архив по-прежнему запирает."""
    from app.models.task_block import BLOCK_MEDIA, BLOCK_PHOTO, BLOCK_VIDEO, TaskBlock

    client, user = auth_client
    old_cycle = _cycle(
        db, user, title="Цикл 3",
        starts_on=TODAY - timedelta(days=10), ends_on=TODAY - timedelta(days=2),
    )
    _cycle(
        db, user, title="Следующий цикл",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=5),
    )
    task = _task_in_topic(db, user, old_cycle, title="Задание прошлого цикла")
    blocks = {
        kind: TaskBlock(task_id=task.id, block_type=kind, title=kind, sort_order=order)
        for order, kind in enumerate((BLOCK_VIDEO, BLOCK_PHOTO, BLOCK_MEDIA))
    }
    db.add_all(blocks.values())
    db.commit()

    assert client.post(f"/cabinet/tracker/blocks/{blocks[BLOCK_VIDEO].id}/watched").status_code == 200
    assert client.post(f"/cabinet/tracker/blocks/{blocks[BLOCK_PHOTO].id}/done").status_code == 200
    assert client.post(f"/cabinet/tracker/blocks/{blocks[BLOCK_MEDIA].id}/done").status_code == 200
    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 403
