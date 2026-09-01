"""Агрегатор проверки — этап 2: скелет DTO и адаптер `_task_block_items`.

`plans/2026-09-01-apparchi-student-centric-review.md`, этап 2.
"""

from app.models.tracker import TrackerTask
from app.services.review_aggregate import DOMAIN_TASK_BLOCK, ReviewItem, _task_block_items
from app.services.task_blocks import save_response, set_reviewed, sync_blocks


def _task(db, title="Материал недели", subject="Рисунок") -> TrackerTask:
    task = TrackerTask(title=title, kind="material", subject=subject)
    db.add(task)
    db.flush()
    return task


def _question(body, **extra):
    from app.models.task_block import BLOCK_QUESTION

    return {"block_type": BLOCK_QUESTION, "body": body, **extra}


def test_task_block_items_maps_unreviewed_answer_to_dto(db, user_factory):
    task = _task(db)
    student = user_factory(vk_id=800_101, name="Ученик Аня")
    [block] = sync_blocks(db, task_id=task.id, items=[_question("Что было главным?")])
    db.commit()

    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"text": "Свет и тень"}},
    )
    db.commit()

    items = _task_block_items(db)

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ReviewItem)
    assert item.domain == DOMAIN_TASK_BLOCK
    assert item.student_id == student.id
    assert item.title == "Материал недели"
    assert item.subject == "Рисунок"
    assert item.is_reviewed is False
    assert item.submitted_at is not None
    assert item.review_url == f"/cabinet/staff/review?student={student.id}&only=all"


def test_task_block_items_reflects_reviewed_flag(db, user_factory):
    task = _task(db)
    student = user_factory(vk_id=800_102, name="Ученик Боря")
    curator = user_factory(vk_id=800_103, name="Куратор", role_name="куратор")
    [block] = sync_blocks(db, task_id=task.id, items=[_question("Вопрос")])
    db.commit()
    response = save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"text": "Ответ"}},
    )
    db.commit()

    from app.models.task_block import TaskBlockAnswer
    answer = db.query(TaskBlockAnswer).filter(
        TaskBlockAnswer.response_id == response.id
    ).one()
    set_reviewed(db, answer_id=answer.id, user_id=curator.id, reviewed=True)
    db.commit()

    items = _task_block_items(db)
    assert items[0].is_reviewed is True


def test_task_block_items_respects_curator_scope(db, user_factory):
    task = _task(db)
    own_curator = user_factory(vk_id=800_104, name="Куратор свой", role_name="куратор")
    own_student = user_factory(vk_id=800_105, name="Свой ученик")
    own_student.curator_id = own_curator.id

    foreign_student = user_factory(vk_id=800_106, name="Чужой ученик")
    db.commit()

    [block] = sync_blocks(db, task_id=task.id, items=[_question("Вопрос")])
    db.commit()
    for student in (own_student, foreign_student):
        save_response(
            db, task_id=task.id, user_id=student.id, blocks=[block],
            answers={block.id: {"text": "Ответ"}},
        )
    db.commit()

    items = _task_block_items(db, curator_id=own_curator.id)

    assert [i.student_id for i in items] == [own_student.id]


def test_task_block_items_empty_when_nothing_answered(db):
    _task(db)
    db.commit()

    assert _task_block_items(db) == []
