"""Ученик одного предмета (Александрия 23.10.2026).

«Ребёнок занимается только по одному предмету… у нас там есть рисунок и
композиция, но при этом есть также общий блок». Поле `User.study_subject`:
пусто — все предметы, «Рисунок» или «Композиция» — только он и «Общее».
Предмет шага — `block.subject or task.subject`, правило одно —
`task_blocks.is_block_open_to`.
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import TaskBlock, TaskBlockStudent
from app.services.cycle_feed import _subject_tabs, build_cycle_feed
from app.services.program import day_bounds
from app.services.task_blocks import BlockViewer, close_block_for_user, tasks_closed_to
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
    block = TaskBlock(
        task_id=task.id, block_type="upload", title=title, body="задание",
        sort_order=order, is_required=True, subject=subject,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _statuses(db, user):
    return {
        (step["block"].title if step["block"] else step["task"].title): step["status"]
        for step in build_cycle_feed(
            db, user_id=user.id, user_tariff=user.tariff,
            start=CYCLE_START, end=CYCLE_END,
        )
    }


def _program(db, user):
    """Общее → композиция → рисунок → снова общее, как в цикле школы."""
    _cycle(db, user)
    general = _task(db, user, title="Архитектурный рисунок", order=0)
    intro = _step(db, general, title="Введение")
    comp = _task(db, user, title="Композиция", subject=COMPOSITION, order=1)
    comp_1 = _step(db, comp, title="Композиция 1")
    draw = _task(db, user, title="Рисунок", subject=DRAWING, order=2)
    draw_1 = _step(db, draw, title="Рисунок 1")
    final = _task(db, user, title="Итог", order=3)
    _step(db, final, title="Итоговое общее")
    return {"intro": intro, "comp": comp, "comp_1": comp_1, "draw": draw, "draw_1": draw_1}


def _only(db, user, subject):
    user.study_subject = subject
    db.commit()


def test_drawing_student_sees_general_and_drawing_only(db, regular_user):
    _program(db, regular_user)
    _only(db, regular_user, DRAWING)

    statuses = _statuses(db, regular_user)

    assert "Композиция 1" not in statuses
    assert {"Введение", "Рисунок 1", "Итоговое общее"} <= statuses.keys()


def test_block_without_own_subject_takes_task_subject(db, regular_user):
    """Блок без предмета в задании «Композиция» живёт во вкладке задания —
    и прячется вместе с ним."""
    _cycle(db, regular_user)
    comp = _task(db, regular_user, title="Композиция", subject=COMPOSITION)
    _step(db, comp, title="Без своего предмета")
    _only(db, regular_user, DRAWING)

    assert "Без своего предмета" not in _statuses(db, regular_user)


def test_block_subject_beats_task_subject(db, regular_user):
    _cycle(db, regular_user)
    comp = _task(db, regular_user, title="Композиция", subject=COMPOSITION)
    _step(db, comp, title="Рисунок внутри", subject=DRAWING)
    _only(db, regular_user, DRAWING)

    assert "Рисунок внутри" in _statuses(db, regular_user)


def test_composition_only_task_does_not_exist_for_drawing_student(db, regular_user):
    program = _program(db, regular_user)
    _only(db, regular_user, DRAWING)
    viewer = BlockViewer(db, user_id=regular_user.id, tariff=regular_user.tariff)

    closed = tasks_closed_to(
        db, [program["comp"].id, program["draw"].id], viewer=viewer
    )

    assert closed == {program["comp"].id}


def test_final_general_step_does_not_wait_for_hidden_composition(db, regular_user):
    program = _program(db, regular_user)
    _only(db, regular_user, DRAWING)
    for block in (program["intro"], program["draw_1"]):
        close_block_for_user(db, block=block, user_id=regular_user.id, source="manual")
    db.commit()

    assert _statuses(db, regular_user)["Итоговое общее"] == "current"


def test_named_student_sees_other_subject(db, regular_user):
    program = _program(db, regular_user)
    db.add(TaskBlockStudent(block_id=program["comp_1"].id, user_id=regular_user.id))
    _only(db, regular_user, DRAWING)

    assert "Композиция 1" in _statuses(db, regular_user)


def test_empty_study_subject_sees_everything(db, regular_user):
    _program(db, regular_user)

    statuses = _statuses(db, regular_user)

    assert {"Введение", "Композиция 1", "Рисунок 1", "Итоговое общее"} <= statuses.keys()


def test_tabs_for_single_subject_student():
    steps = [{"subject": None}, {"subject": DRAWING}]

    assert [tab["label"] for tab in _subject_tabs(steps, only=DRAWING)] == ["Общее", DRAWING]
    assert [tab["label"] for tab in _subject_tabs(steps)] == ["Общее", COMPOSITION, DRAWING]
    assert _subject_tabs([{"subject": None}], only=DRAWING) == []
