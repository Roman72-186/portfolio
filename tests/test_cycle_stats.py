"""Прохождение цикла (владелец 03.09.2026).

«Нам нужно с каждого цикла вытаскивать вообще сколько людей там посмотрело,
сколько людей там в итоге загрузили задание… будем смотреть с разных тарифов,
сколько людей не досмотрело».
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import BLOCK_TEXT, TaskBlock
from app.services.cycle_stats import cycle_stats
from app.services.program import day_bounds
from app.services.task_blocks import close_block_for_user
from app.services.tracker import close_task_for_user, create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=2)
CYCLE_END = TODAY + timedelta(days=5)


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    topic = LearningTopic(
        title="Цикл 19–21", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _task(db, owner, *, title="Теория", due_on=TODAY):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(due_on)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, *, title="Видео", order=1):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title=title, body="текст",
        sort_order=order, is_required=True,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def test_empty_cycle_has_no_steps(db, regular_user):
    topic = _cycle(db, regular_user)

    stats = cycle_stats(db, topic)

    assert stats["steps"] == []
    assert stats["finished"] == 0


def test_step_counts_who_closed_it(db, regular_user, user_factory):
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    block = _block(db, task)
    other = user_factory(vk_id=771_001, role_name="ученик")
    close_block_for_user(db, block=block, user_id=regular_user.id, source="manual")
    db.commit()

    stats = cycle_stats(db, topic)

    assert len(stats["steps"]) == 1
    step = stats["steps"][0]
    assert step["done"] == 1
    assert step["total"] == stats["students"] >= 2
    assert other.id != regular_user.id


def test_stats_split_by_tariff(db, regular_user, user_factory):
    """«Будем смотреть с разных тарифов, сколько людей не досмотрело»."""
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    block = _block(db, task)
    regular_user.tariff = "МАКСИМУМ"
    second = user_factory(vk_id=771_002, role_name="ученик")
    second.tariff = "Я С ВАМИ"
    db.commit()
    close_block_for_user(db, block=block, user_id=regular_user.id, source="manual")
    db.commit()

    stats = cycle_stats(db, topic)

    by_tariff = stats["steps"][0]["by_tariff"]
    assert by_tariff["МАКСИМУМ"] == 1
    assert by_tariff["Я С ВАМИ"] == 0


def test_task_without_blocks_is_one_step(db, regular_user):
    """Экран статистики и экран ученика считают задания одинаково."""
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user, title="Видео недели")
    close_task_for_user(db, task, regular_user.id, source="manual")
    db.commit()

    stats = cycle_stats(db, topic)

    assert [s["title"] for s in stats["steps"]] == ["Видео недели"]
    assert stats["steps"][0]["done"] == 1


def test_finished_counts_only_those_who_closed_everything(db, regular_user, user_factory):
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    first = _block(db, task, title="Первый", order=1)
    _block(db, task, title="Второй", order=2)
    user_factory(vk_id=771_003, role_name="ученик")
    close_block_for_user(db, block=first, user_id=regular_user.id, source="manual")
    db.commit()

    stats = cycle_stats(db, topic)

    assert stats["finished"] == 0


def test_task_outside_the_period_is_not_counted(db, regular_user):
    topic = _cycle(db, regular_user)
    _task(db, regular_user, title="Другой месяц", due_on=TODAY + timedelta(days=40))

    stats = cycle_stats(db, topic)

    assert stats["steps"] == []


def test_undated_cycle_task_is_counted_by_topic_id(db, regular_user):
    """Задание внутри цикла (10.09.2026) заводится без даты, `topic_id`
    указывает прямо на цикл — статистика обязана находить его так же, как
    находит старые датные задания по совпадению `due_at` с периодом."""
    topic = _cycle(db, regular_user)
    task = create_task(
        db, title="Задание цикла", user_id=regular_user.id, kind="material",
        due_at=None, topic_id=topic.id, assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    _block(db, task)

    stats = cycle_stats(db, topic)

    assert len(stats["steps"]) == 1
    assert stats["steps"][0]["title"] == "Видео"  # заголовок блока из _block()


# ── экран ───────────────────────────────────────────────────────────────────

def test_stats_page_opens_for_admin(admin_client, db, regular_user):
    client, _ = admin_client
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    _block(db, task, title="Видео знакомства")

    page = client.get(f"/cabinet/staff/program/cycles/{topic.id}/stats")

    assert page.status_code == 200
    assert "Видео знакомства" in page.text
    assert "Цикл 19–21" in page.text


def test_stats_page_404_for_missing_cycle(admin_client):
    client, _ = admin_client

    assert client.get("/cabinet/staff/program/cycles/999999/stats").status_code == 404


def test_stats_page_closed_for_students(auth_client, db, regular_user):
    client, _ = auth_client
    topic = _cycle(db, regular_user)

    resp = client.get(
        f"/cabinet/staff/program/cycles/{topic.id}/stats", follow_redirects=False
    )

    assert resp.status_code in (302, 403)
