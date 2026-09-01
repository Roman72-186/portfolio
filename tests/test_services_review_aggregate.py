"""Агрегатор проверки — этапы 2-3: DTO и все четыре адаптера.

`plans/2026-09-01-apparchi-student-centric-review.md`, этапы 2-3.
"""

from datetime import date, datetime, timedelta, timezone

from app.models.exam_cycle import ExamCycle
from app.models.homework_submission import HomeworkSubmission
from app.models.tracker import ITEM_HOMEWORK, SOURCE_HOMEWORK, TrackerTask
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.review_aggregate import (
    DOMAIN_EXAM_CYCLE,
    DOMAIN_HOMEWORK,
    DOMAIN_TASK_BLOCK,
    DOMAIN_WORK,
    ReviewItem,
    aggregate_student_review_counts,
    student_review_items,
    week_bounds,
    _exam_cycle_items,
    _homework_items,
    _task_block_items,
    _work_items,
)
from app.services.task_blocks import save_response, set_reviewed, sync_blocks
from app.services.tracker import create_homework, create_task, set_homework_images


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


# --- _work_items --------------------------------------------------------


def _work(db, user_id, *, score=None, viewed_at=None, work_type=WORK_TYPE_MOCK_EXAM, subject="Рисунок"):
    w = Work(
        user_id=user_id, work_type=work_type, month="сентябрь", year=2026,
        filename="final.jpg", s3_url="https://s3.example.com/final.jpg",
        subject=subject, status="success", is_final=True,
        score=score, viewed_at=viewed_at,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


def test_work_items_unscored_is_unreviewed(db, user_factory):
    student = user_factory(vk_id=810_101, name="Ученик")
    _work(db, student.id, score=None)

    items = _work_items(db)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ReviewItem)
    assert item.domain == DOMAIN_WORK
    assert item.student_id == student.id
    assert item.is_reviewed is False
    assert item.review_url == f"/cabinet/students?student={student.id}&tab=mock-exams"


def test_work_items_excludes_portfolio_before_after(db, user_factory):
    """Advisor-ревью 01.09.2026: портфолио (before/after) никогда не получает
    score в интерфейсе (только галерея месяцев) — включение навесило бы
    каждому ученику постоянный «непроверено» без способа снять."""
    student = user_factory(vk_id=810_150, name="Ученик")
    _work(db, student.id, score=None, work_type="before")
    _work(db, student.id, score=None, work_type="after")

    assert _work_items(db) == []


def test_work_items_scored_is_reviewed(db, user_factory):
    student = user_factory(vk_id=810_102, name="Ученик")
    _work(db, student.id, score=80)

    assert _work_items(db)[0].is_reviewed is True


def test_work_items_viewed_without_score_is_reviewed(db, user_factory):
    """Решение владельца 01.09.2026: «просмотрено» без оценки тоже снимает
    работу с непроверенных."""
    student = user_factory(vk_id=810_103, name="Ученик")
    _work(db, student.id, score=None, viewed_at=datetime.now(timezone.utc))

    assert _work_items(db)[0].is_reviewed is True


def test_work_items_respects_curator_scope(db, user_factory):
    own_curator = user_factory(vk_id=810_104, name="Куратор свой", role_name="куратор")
    own_student = user_factory(vk_id=810_105, name="Свой ученик")
    own_student.curator_id = own_curator.id
    foreign_student = user_factory(vk_id=810_106, name="Чужой ученик")
    db.commit()
    _work(db, own_student.id)
    _work(db, foreign_student.id)

    items = _work_items(db, curator_id=own_curator.id)
    assert [i.student_id for i in items] == [own_student.id]


def test_work_items_filters_by_week(db, user_factory):
    student = user_factory(vk_id=810_107, name="Ученик")
    old = _work(db, student.id)
    old.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.commit()

    week_start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=7)
    assert _work_items(db, week_start=week_start, week_end=week_end) == []


# --- _homework_items -----------------------------------------------------


def _homework_submission(db, user_id, *, status="submitted", subject=None):
    homework = create_homework(db, title="Нарисуй куб", user_id=user_id)
    set_homework_images(db, homework, [{"url": "https://s3.example.com/ref.jpg", "path": "ref.jpg"}])
    task = create_task(
        db, title="Нарисуй куб", user_id=user_id, kind=ITEM_HOMEWORK,
        source_kind=SOURCE_HOMEWORK, source_id=homework.id, assign_to_all=True,
        subject=subject,
    )
    task.is_published = True
    db.commit()
    submission = HomeworkSubmission(
        homework_id=homework.id, tracker_task_id=task.id, user_id=user_id,
        status=status, submitted_at=datetime.now(timezone.utc),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def test_homework_items_unaccepted_is_unreviewed(db, user_factory):
    student = user_factory(vk_id=820_101, name="Ученик")
    submission = _homework_submission(db, student.id, status="submitted")

    items = _homework_items(db)
    assert len(items) == 1
    item = items[0]
    assert item.domain == DOMAIN_HOMEWORK
    assert item.is_reviewed is False
    assert item.review_url == f"/cabinet/staff/homework/submissions/{submission.id}"


def test_homework_items_accepted_is_reviewed(db, user_factory):
    student = user_factory(vk_id=820_102, name="Ученик")
    _homework_submission(db, student.id, status="accepted")

    assert _homework_items(db)[0].is_reviewed is True


def test_homework_items_skips_lazily_created_submission_without_photo(db, user_factory):
    """Сдача заводится лениво при первом заходе на страницу, до загрузки
    фото submitted_at пуст — такую строку показывать преподавателю рано."""
    student = user_factory(vk_id=820_103, name="Ученик")
    homework = create_homework(db, title="Нарисуй куб", user_id=student.id)
    set_homework_images(db, homework, [{"url": "https://s3.example.com/ref.jpg", "path": "ref.jpg"}])
    task = create_task(
        db, title="Нарисуй куб", user_id=student.id, kind=ITEM_HOMEWORK,
        source_kind=SOURCE_HOMEWORK, source_id=homework.id, assign_to_all=True,
    )
    task.is_published = True
    db.commit()
    submission = HomeworkSubmission(
        homework_id=homework.id, tracker_task_id=task.id, user_id=student.id,
        status="submitted", submitted_at=None,
    )
    db.add(submission)
    db.commit()

    assert _homework_items(db) == []


# --- _exam_cycle_items -----------------------------------------------------


def _cycle(db, user_id, *, closed=False, viewed_at=None, on_revision=False, subject="Рисунок"):
    c = ExamCycle(
        user_id=user_id, subject=subject, started_at=date(2026, 9, 1),
        closed_at=datetime.now(timezone.utc) if closed else None,
        viewed_at=viewed_at,
        revision_requested_at=datetime.now(timezone.utc) if on_revision else None,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_exam_cycle_items_open_is_unreviewed(db, user_factory):
    student = user_factory(vk_id=830_101, name="Ученик")
    cycle = _cycle(db, student.id, closed=False)

    items = _exam_cycle_items(db)
    assert len(items) == 1
    item = items[0]
    assert item.domain == DOMAIN_EXAM_CYCLE
    assert item.is_reviewed is False
    assert item.review_url == f"/cabinet/students/{student.id}/cycles"


def test_exam_cycle_items_closed_is_reviewed(db, user_factory):
    student = user_factory(vk_id=830_102, name="Ученик")
    _cycle(db, student.id, closed=True)

    assert _exam_cycle_items(db)[0].is_reviewed is True


def test_exam_cycle_items_viewed_without_closing_is_reviewed(db, user_factory):
    student = user_factory(vk_id=830_103, name="Ученик")
    _cycle(db, student.id, closed=False, viewed_at=datetime.now(timezone.utc))

    assert _exam_cycle_items(db)[0].is_reviewed is True


def test_exam_cycle_items_on_revision_is_unreviewed_even_if_closed(db, user_factory):
    student = user_factory(vk_id=830_104, name="Ученик")
    _cycle(db, student.id, closed=True, on_revision=True)

    assert _exam_cycle_items(db)[0].is_reviewed is False


# --- aggregate_student_review_counts (этап 4) --------------------------------


def test_student_with_zero_unchecked_is_not_above_student_with_some(db, user_factory):
    curator = user_factory(vk_id=840_100, name="Куратор", role_name="куратор")
    quiet = user_factory(vk_id=840_101, name="Аня Тихая")
    quiet.curator_id = curator.id
    busy = user_factory(vk_id=840_102, name="Боря Занятой")
    busy.curator_id = curator.id
    db.commit()
    _work(db, busy.id, score=None)  # непроверенная работа только у busy

    rows = aggregate_student_review_counts(db, {"user_id": curator.id, "role_rank": 2})

    by_id = {row["student"].id: row["unchecked"] for row in rows}
    assert by_id[busy.id] == 1
    assert by_id[quiet.id] == 0
    order = [row["student"].id for row in rows]
    assert order.index(busy.id) < order.index(quiet.id)


def test_aggregate_counts_respects_curator_scope(db, user_factory):
    own_curator = user_factory(vk_id=840_103, name="Куратор своя", role_name="куратор")
    own_student = user_factory(vk_id=840_104, name="Свой ученик")
    own_student.curator_id = own_curator.id
    foreign_student = user_factory(vk_id=840_105, name="Чужой ученик")
    db.commit()
    _work(db, own_student.id, score=None)
    _work(db, foreign_student.id, score=None)

    rows = aggregate_student_review_counts(db, {"user_id": own_curator.id, "role_rank": 2})

    assert [row["student"].id for row in rows] == [own_student.id]


def test_aggregate_counts_admin_sees_all_active_students(db, user_factory):
    admin = user_factory(vk_id=840_106, name="Админ", role_name="админ")
    unassigned = user_factory(vk_id=840_107, name="Ученик без куратора")
    db.commit()

    rows = aggregate_student_review_counts(db, {"user_id": admin.id, "role_rank": 4})

    assert unassigned.id in {row["student"].id for row in rows}


def test_aggregate_counts_moderator_scoped_same_as_curator(db, user_factory):
    """Advisor-ревью 01.09.2026: до фикса `_accessible_students` (rank != 2 →
    видно всех) и счётчик (rank < 4 → скоуп по curator_id) расходились на
    ранге 3 — модератор видел всю школу с вечным «всё проверено»."""
    moderator = user_factory(vk_id=840_108, name="Модератор", role_name="модератор")
    own_student = user_factory(vk_id=840_109, name="Свой ученик")
    own_student.curator_id = moderator.id
    foreign_student = user_factory(vk_id=840_110, name="Чужой ученик")
    db.commit()

    rows = aggregate_student_review_counts(db, {"user_id": moderator.id, "role_rank": 3})

    assert [row["student"].id for row in rows] == [own_student.id]


# --- week_bounds / student_review_items (этап 5) -----------------------------


def test_week_bounds_covers_monday_to_sunday():
    start, end = week_bounds(date(2026, 9, 3))  # четверг
    assert start.date() == date(2026, 8, 31)  # понедельник той же недели
    assert end.date() == date(2026, 9, 7)      # следующий понедельник


def test_student_review_items_merges_all_domains_unreviewed_first(db, user_factory):
    student = user_factory(vk_id=850_101, name="Ученик")
    _work(db, student.id, score=None)
    _cycle(db, student.id, closed=True)  # проверено

    items = student_review_items(db, student_id=student.id)

    assert {i.domain for i in items} == {DOMAIN_WORK, DOMAIN_EXAM_CYCLE}
    assert items[0].is_reviewed is False
    assert items[-1].is_reviewed is True


def test_student_review_items_only_this_student(db, user_factory):
    student = user_factory(vk_id=850_102, name="Ученик")
    other = user_factory(vk_id=850_103, name="Другой ученик")
    _work(db, student.id)
    _work(db, other.id)

    items = student_review_items(db, student_id=student.id)

    assert all(i.student_id == student.id for i in items)
