"""Очередь сдач в блоках (`task_blocks.submission_review_queue`) для экрана проверки.

Код-ревью 28.09.2026, P2 № 11: очередь показывала сдачи по удалённым
заданиям и брала конец недели включительно (`<=`). `week_bounds` отдаёт
«следующий понедельник 00:00» как границу-исключение, и сдача ровно в полночь
попадала в две недели сразу. Соседние адаптеры `review_aggregate.py` и очередь
ответов (`review_queue`) сравнивают через `<` и прячут удалённые задания.
"""
from datetime import date, datetime, timezone

from app.models.task_block import BLOCK_UPLOAD, TaskBlock, TaskBlockState, TaskBlockSubmission
from app.services.review_aggregate import week_bounds
from app.services.task_blocks import submission_review_queue
from app.services.tracker import create_task


def _submission(db, owner, student, *, submitted_at, title="Задание"):
    task = create_task(db, title=title, user_id=owner.id, kind="material", assign_to_all=True)
    task.is_published = True
    block = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Работа", sort_order=1)
    db.add(block)
    db.flush()
    submission = TaskBlockSubmission(block_id=block.id, user_id=student.id, submitted_at=submitted_at)
    db.add(submission)
    db.commit()
    return task, block, submission


def test_submission_at_midnight_belongs_to_the_next_week_only(db, user_factory):
    owner = user_factory(vk_id=960_001, name="ГП", role_name="админ")
    student = user_factory(vk_id=960_002, name="Ученик")
    this_start, this_end = week_bounds(date(2026, 9, 21))
    next_start, next_end = week_bounds(date(2026, 9, 28))
    assert this_end == next_start
    _, _, submission = _submission(db, owner, student, submitted_at=this_end)

    this_week = submission_review_queue(db, week_start=this_start, week_end=this_end)
    next_week = submission_review_queue(db, week_start=next_start, week_end=next_end)

    assert [i["submission_id"] for i in this_week] == []
    assert [i["submission_id"] for i in next_week] == [submission.id]


def test_submission_of_a_deleted_task_leaves_the_queue(db, user_factory):
    owner = user_factory(vk_id=960_003, name="ГП", role_name="админ")
    student = user_factory(vk_id=960_004, name="Ученик")
    task, _, submission = _submission(
        db, owner, student, submitted_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
    )
    assert [i["submission_id"] for i in submission_review_queue(db)] == [submission.id]

    task.deleted_at = datetime.now(timezone.utc)
    db.commit()

    assert submission_review_queue(db) == []


def test_overrun_is_read_for_each_row_in_one_query(db, user_factory):
    """P3 того же ревью: `get_state` на каждую строку (до 200 походов в базу).
    Состояние блока берётся пачкой, и у каждой строки — своё, а не соседа."""
    owner = user_factory(vk_id=960_005, name="ГП", role_name="админ")
    first = user_factory(vk_id=960_006, name="Первый")
    second = user_factory(vk_id=960_007, name="Второй")
    when = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
    _, block, _ = _submission(db, owner, first, submitted_at=when)
    db.add(TaskBlockSubmission(block_id=block.id, user_id=second.id, submitted_at=when))
    db.add(TaskBlockState(block_id=block.id, user_id=first.id))
    db.commit()

    from sqlalchemy import event

    statements = []
    engine = db.get_bind()
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        items = submission_review_queue(db)
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert len(items) == 2
    state_queries = [s for s in statements if "task_block_states" in s]
    assert len(state_queries) == 1
