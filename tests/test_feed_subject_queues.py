"""Очередь ленты делится по предметам (владелец 06.10.2026).

Жалоба преподавателя: после общего задания по архитектурному рисунку должны
открываться сразу первые блоки рисунка и композиции, а ребёнок, «если он не
прошёл все блоки по композиции, не может открыть ни одного блока по рисунку».
С 01.10.2026 очередь была одна на весь цикл, и композиция, стоявшая выше,
держала весь рисунок.

Правило (`task_blocks.shares_queue`): рисунок ждёт только рисунок, композиция
— только композицию; шаг без предмета («Общее») держит всё, что ниже, и сам
ждёт всё, что выше.
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import TaskBlock
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.task_blocks import close_block_for_user, feed_state, shares_queue
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)

DRAWING = "Рисунок"
COMPOSITION = "Композиция"


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    db.add(LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    ))
    db.commit()


def _task(db, owner, *, title, subject=None, order=0):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY + timedelta(days=2))[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    task.subject = subject
    task.sort_order = order
    db.commit()
    db.refresh(task)
    return task


def _step(db, task, *, title, subject=None, order=0):
    """Обязательная сдача работы — шаг, который держит очередь. Текст и ссылка
    обязательными не бывают (владелец 01.10.2026)."""
    block = TaskBlock(
        task_id=task.id, block_type="upload", title=title, body="задание",
        sort_order=order, is_required=True, subject=subject,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _feed(db, user):
    return build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )


def _statuses(db, user):
    return {
        (step["block"].title if step["block"] else step["task"].title): step["status"]
        for step in _feed(db, user)
    }


def _program(db, user):
    """Общее задание → две композиции → два рисунка, как в цикле из жалобы."""
    _cycle(db, user)
    general = _task(db, user, title="Архитектурный рисунок", order=0)
    intro = _step(db, general, title="Введение")
    comp = _task(db, user, title="Композиция", subject=COMPOSITION, order=1)
    comp_1 = _step(db, comp, title="Композиция 1", order=0)
    _step(db, comp, title="Композиция 2", order=1)
    draw = _task(db, user, title="Рисунок", subject=DRAWING, order=2)
    draw_1 = _step(db, draw, title="Рисунок 1", order=0)
    _step(db, draw, title="Рисунок 2", order=1)
    return intro, comp_1, draw_1


def test_shares_queue_rule():
    assert shares_queue(None, DRAWING)
    assert shares_queue(DRAWING, None)
    assert shares_queue("", DRAWING)
    assert shares_queue(DRAWING, DRAWING)
    assert not shares_queue(COMPOSITION, DRAWING)


def test_general_step_holds_both_subjects(db, regular_user):
    _program(db, regular_user)

    statuses = _statuses(db, regular_user)

    assert statuses["Введение"] == "current"
    assert statuses["Композиция 1"] == "locked"
    assert statuses["Рисунок 1"] == "locked"


def test_after_general_step_both_subjects_open_at_once(db, regular_user):
    """Повтор жалобы: композиция не пройдена, а рисунок уже открыт."""
    intro, _, _ = _program(db, regular_user)
    close_block_for_user(db, block=intro, user_id=regular_user.id, source="manual")
    db.commit()

    statuses = _statuses(db, regular_user)

    assert statuses["Композиция 1"] == "current"
    assert statuses["Рисунок 1"] == "current"
    assert statuses["Композиция 2"] == "locked"
    assert statuses["Рисунок 2"] == "locked"


def test_drawing_goes_ahead_without_composition(db, regular_user):
    intro, _, draw_1 = _program(db, regular_user)
    close_block_for_user(db, block=intro, user_id=regular_user.id, source="manual")
    close_block_for_user(db, block=draw_1, user_id=regular_user.id, source="manual")
    db.commit()

    statuses = _statuses(db, regular_user)

    assert statuses["Рисунок 2"] == "current"
    assert statuses["Композиция 1"] == "current"
    assert statuses["Композиция 2"] == "locked"


def test_locked_drawing_names_drawing_holder_not_composition(db, regular_user):
    intro, _, _ = _program(db, regular_user)
    close_block_for_user(db, block=intro, user_id=regular_user.id, source="manual")
    db.commit()

    steps = {step["block"].title: step for step in _feed(db, regular_user)}

    assert steps["Рисунок 2"]["blocked_by"]["title"] == "Рисунок 1"
    assert steps["Композиция 2"]["blocked_by"]["title"] == "Композиция 1"


def test_general_step_below_waits_for_every_subject(db, regular_user):
    intro, comp_1, draw_1 = _program(db, regular_user)
    final = _task(db, regular_user, title="Итог", order=3)
    _step(db, final, title="Итоговое задание")
    for block in (intro, comp_1, draw_1):
        close_block_for_user(db, block=block, user_id=regular_user.id, source="manual")
    db.commit()

    steps = {step["block"].title: step for step in _feed(db, regular_user)}

    assert steps["Итоговое задание"]["status"] == "locked"
    assert steps["Итоговое задание"]["blocked_by"]["title"] == "Композиция 2"


def test_required_task_without_blocks_holds_only_its_subject(db, regular_user):
    """Обязательное задание без блоков (видео) композиции рисунок не держит."""
    _cycle(db, regular_user)
    _task(db, regular_user, title="Видео по композиции", subject=COMPOSITION, order=0)
    draw = _task(db, regular_user, title="Рисунок", subject=DRAWING, order=1)
    _step(db, draw, title="Рисунок 1")
    comp = _task(db, regular_user, title="Композиция", subject=COMPOSITION, order=2)
    _step(db, comp, title="Композиция 1")

    statuses = _statuses(db, regular_user)

    assert statuses["Рисунок 1"] == "current"
    assert statuses["Композиция 1"] == "locked"


def test_task_panel_splits_queue_by_block_subject(db, regular_user):
    """Панель задания в трекере (`feed_state`) считает очередь так же."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Смешанное задание")
    _step(db, task, title="Композиция 1", subject=COMPOSITION, order=0)
    _step(db, task, title="Рисунок 1", subject=DRAWING, order=1)
    _step(db, task, title="Композиция 2", subject=COMPOSITION, order=2)

    statuses = {
        row["block"].title: row["status"]
        for row in feed_state(
            db, task_id=task.id, user_id=regular_user.id,
            user_tariff=regular_user.tariff,
        )
    }

    assert statuses["Рисунок 1"] != "locked"
    assert statuses["Композиция 2"] == "locked"
