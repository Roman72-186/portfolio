"""Агрегатор «проверить всё по ученику за один заход» (созвон 01.09.2026).

Один адаптер на домен, общий формат на выходе — план
`plans/2026-09-01-apparchi-student-centric-review.md`, раздел «Архитектура».
Без новой таблицы: «непроверено» уже есть как предикат в каждом домене
(`Work.score IS NULL`, `TaskBlockAnswer.reviewed_at IS NULL`, ...), считать на
лету дешевле, чем городить индекс-таблицу, которая разойдётся с источником
при прямой правке в обход сервиса.

Период — календарная неделя (решение владельца 01.09.2026, вопрос 3): каждый
адаптер фильтрует по своему естественному якорю даты (`created_at`,
`submitted_at`, `started_at`), `week_start`/`week_end` — обычные границы
календарной недели, без привязки к `LearningTopic`.

Диалог `Feedback`/`HomeworkFeedback` в DTO не прокидывается: решение 5 —
только ссылка на существующий UI, самого диалога на новом экране нет, и
поэтому отдельный признак «нужен ответ» этому экрану не нужен.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session as DBSession

DOMAIN_TASK_BLOCK = "task_block"
DOMAIN_WORK = "work"
DOMAIN_HOMEWORK = "homework"
DOMAIN_EXAM_CYCLE = "exam_cycle"

# С этого ранга видно всех учеников без ограничения по curator_id. Ранг 3
# (модератор) попадает под то же ограничение, что куратор — владелец про
# него не говорил (решение 1 было только про куратора), и показать меньше
# безопаснее, чем чужое (тот же приём, что student_review.py::_check_student_access).
FULL_ACCESS_RANK = 4

_WORK_TYPE_TITLES = {"mock_exam": "Пробник", "retake": "Отработка"}
_WORK_TYPE_TABS = {"mock_exam": "mock-exams", "retake": "retakes"}


@dataclass(frozen=True)
class ReviewItem:
    """Общий формат строки в едином списке проверки, один на все домены.

    `question`/`chosen`/`correct`/`text` заполнены только у `task_block`
    (снос отдельного экрана `/cabinet/staff/review` 02.09.2026 — карточку
    ответа теперь рисует сам `staff_student_review_detail.html`, `review_url`
    у этого домена не используется)."""

    domain: str
    item_id: int
    student_id: int
    title: str
    subject: str | None
    submitted_at: datetime | None
    is_reviewed: bool
    review_url: str
    question: str | None = None
    chosen: list[str] | None = None
    correct: list[str] | None = None
    text: str | None = None


def _task_block_items(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> list[ReviewItem]:
    """Ответы на блоки заданий — обёртка над `task_blocks.py::review_queue`.

    Запрос, скоуп куратора и фильтры уже сделаны там, здесь только приведение
    к общему DTO. `only_unreviewed=False`: агрегатору нужны и проверенные
    строки — это он сам решает, что показать выше.
    """
    from app.services.task_blocks import review_queue

    raw = review_queue(
        db,
        curator_id=curator_id,
        only_unreviewed=False,
        subject=subject,
        student_id=student_id,
        tariff=tariff,
        week_start=week_start,
        week_end=week_end,
        limit=100_000,
    )
    return [
        ReviewItem(
            domain=DOMAIN_TASK_BLOCK,
            item_id=row["answer_id"],
            student_id=row["student_id"],
            title=row["task_title"],
            subject=row["subject"],
            submitted_at=row["answered_at"],
            is_reviewed=row["reviewed"],
            review_url="",
            question=row["question"],
            chosen=row["chosen"],
            correct=row["correct"],
            text=row["text"],
        )
        for row in raw
    ]


def _work_items(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> list[ReviewItem]:
    """Пробник и отработка — не портфолио: `before`/`after` в интерфейсе никогда
    не получают `score` (нет такой формы, только галерея месяцев), включать их
    сюда значило бы навесить каждому ученику вечный «непроверено» без способа
    снять (advisor-ревью 01.09.2026). Непроверено — `score IS NULL`;
    «просмотрено» без оценки (миграция `744b7e5e4961`) тоже снимает строку с
    «непроверенных»."""
    from app.models.user import User
    from app.models.work import WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE, Work

    q = (
        db.query(Work, User)
        .join(User, User.id == Work.user_id)
        .filter(
            Work.status == "success", Work.is_final == True,  # noqa: E712
            Work.work_type.in_((WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE)),
        )
    )
    if curator_id is not None:
        q = q.filter(User.curator_id == curator_id)
    if student_id is not None:
        q = q.filter(User.id == student_id)
    if subject:
        q = q.filter(Work.subject == subject)
    if tariff:
        q = q.filter(User.tariff == tariff)
    if week_start is not None:
        q = q.filter(Work.created_at >= week_start)
    if week_end is not None:
        q = q.filter(Work.created_at < week_end)

    items = []
    for work, student in q.order_by(Work.created_at.desc()).all():
        tab = _WORK_TYPE_TABS.get(work.work_type, "mock-exams")
        items.append(ReviewItem(
            domain=DOMAIN_WORK,
            item_id=work.id,
            student_id=student.id,
            title=_WORK_TYPE_TITLES.get(work.work_type, work.work_type),
            subject=work.subject,
            submitted_at=work.created_at,
            is_reviewed=work.score is not None or work.viewed_at is not None,
            review_url=f"/cabinet/students?student={student.id}&tab={tab}",
        ))
    return items


def _homework_items(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> list[ReviewItem]:
    """Сдачи домашки. Непроверено — `status != accepted`. `submitted_at` — дата
    сдачи финального фото, не дедлайн постановки (`TrackerTask.due_at`)."""
    from app.models.homework_submission import STATUS_ACCEPTED, HomeworkSubmission
    from app.models.tracker import TrackerTask
    from app.models.user import User

    q = (
        db.query(HomeworkSubmission, TrackerTask, User)
        .join(TrackerTask, TrackerTask.id == HomeworkSubmission.tracker_task_id)
        .join(User, User.id == HomeworkSubmission.user_id)
        .filter(
            TrackerTask.deleted_at.is_(None),
            HomeworkSubmission.submitted_at.isnot(None),
        )
    )
    if curator_id is not None:
        q = q.filter(User.curator_id == curator_id)
    if student_id is not None:
        q = q.filter(User.id == student_id)
    if subject:
        q = q.filter(TrackerTask.subject == subject)
    if tariff:
        q = q.filter(User.tariff == tariff)
    if week_start is not None:
        q = q.filter(HomeworkSubmission.submitted_at >= week_start)
    if week_end is not None:
        q = q.filter(HomeworkSubmission.submitted_at < week_end)

    items = []
    for submission, task, student in q.order_by(HomeworkSubmission.submitted_at.desc()).all():
        items.append(ReviewItem(
            domain=DOMAIN_HOMEWORK,
            item_id=submission.id,
            student_id=student.id,
            title=task.title,
            subject=task.subject,
            submitted_at=submission.submitted_at,
            is_reviewed=submission.status == STATUS_ACCEPTED,
            review_url=f"/cabinet/staff/homework/submissions/{submission.id}",
        ))
    return items


def _exam_cycle_items(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> list[ReviewItem]:
    """Циклы Пробника. Непроверено — открыт (`closed_at IS NULL`) либо «на
    правке» (`revision_requested_at` без `revision_done_at`); «просмотрено» без
    закрытия (миграция `744b7e5e4961`) тоже снимает строку с «непроверенных»."""
    from app.models.exam_cycle import ExamCycle
    from app.models.user import User

    q = db.query(ExamCycle, User).join(User, User.id == ExamCycle.user_id)
    if curator_id is not None:
        q = q.filter(User.curator_id == curator_id)
    if student_id is not None:
        q = q.filter(User.id == student_id)
    if subject:
        q = q.filter(ExamCycle.subject == subject)
    if tariff:
        q = q.filter(User.tariff == tariff)
    if week_start is not None:
        q = q.filter(ExamCycle.started_at >= week_start.date())
    if week_end is not None:
        q = q.filter(ExamCycle.started_at < week_end.date())

    items = []
    for cycle, student in q.order_by(ExamCycle.started_at.desc()).all():
        on_revision = cycle.revision_requested_at is not None and cycle.revision_done_at is None
        is_reviewed = (
            (cycle.closed_at is not None and not on_revision)
            or cycle.viewed_at is not None
        )
        submitted_at = cycle.started_at
        if isinstance(submitted_at, date) and not isinstance(submitted_at, datetime):
            # tz-aware: сравнивается с created_at других доменов при сортировке
            # в student_review_items — наивный datetime там уронил бы TypeError.
            submitted_at = datetime.combine(submitted_at, datetime.min.time(), tzinfo=timezone.utc)
        items.append(ReviewItem(
            domain=DOMAIN_EXAM_CYCLE,
            item_id=cycle.id,
            student_id=student.id,
            title=f"Пробник — {cycle.subject}",
            subject=cycle.subject,
            submitted_at=submitted_at,
            is_reviewed=is_reviewed,
            review_url=f"/cabinet/students/{student.id}/cycles",
        ))
    return items


# --- список учеников со счётчиками и сортировкой (этап 4) -------------------


def _accessible_students(db: DBSession, user: dict) -> list:
    """Канонический список — `_get_accessible_students` (решение владельца,
    вопрос 2): куратор (и модератор, `FULL_ACCESS_RANK`) видит `curator_id` +
    `is_active`, admin+ — всех активных."""
    from app.models.role import Role
    from app.models.user import User

    if user["role_rank"] < FULL_ACCESS_RANK:
        return (
            db.query(User)
            .filter(User.curator_id == user["user_id"], User.is_active == True)  # noqa: E712
            .order_by(User.last_name, User.first_name)
            .all()
        )
    student_role = db.query(Role).filter(Role.rank == 1).first()
    if not student_role:
        return []
    return (
        db.query(User)
        .filter(User.role_id == student_role.id, User.is_active == True)  # noqa: E712
        .order_by(User.last_name, User.first_name)
        .all()
    )


def _unreviewed_counts_by_student(db: DBSession, *, curator_id: int | None) -> dict[int, int]:
    """Счётчик непроверенного по каждому ученику, сложенный по всем доменам.

    Переиспользует адаптеры, а не отдельные COUNT-запросы: список учеников на
    экран проверки небольшой (школа, не тысячи учеников), а четыре адаптера
    и так уже написаны и протестированы — второй параллельный набор запросов
    ради счётчика того не стоит.
    """
    from collections import Counter

    counts: Counter[int] = Counter()
    for adapter in (_task_block_items, _work_items, _homework_items, _exam_cycle_items):
        for item in adapter(db, curator_id=curator_id):
            if not item.is_reviewed:
                counts[item.student_id] += 1
    return dict(counts)


def aggregate_student_review_counts(db: DBSession, user: dict) -> list[dict]:
    """Список учеников для карточки экрана проверки: кто сколько не проверил.

    Сортировка — непроверенные выше (по убыванию счётчика), дальше проверенные
    по имени, как в базовом списке.
    """
    curator_id = None if user.get("role_rank", 0) >= FULL_ACCESS_RANK else user["user_id"]
    students = _accessible_students(db, user)
    counts = _unreviewed_counts_by_student(db, curator_id=curator_id)

    rows = [
        {"student": student, "unchecked": counts.get(student.id, 0)}
        for student in students
    ]
    rows.sort(key=lambda row: (-row["unchecked"], (row["student"].last_name or ""), (row["student"].first_name or row["student"].name or "")))
    return rows


# --- единый список по одному ученику (этап 5) --------------------------------


def week_bounds(anchor: date) -> tuple[datetime, datetime]:
    """Границы календарной недели (пн 00:00 — следующий пн 00:00, МСК),
    решение владельца 01.09.2026 (вопрос 3)."""
    from app.services.tz import msk_midnight

    monday = anchor - timedelta(days=anchor.weekday())
    return msk_midnight(monday), msk_midnight(monday + timedelta(days=7))


def student_review_items(
    db: DBSession,
    *,
    student_id: int,
    curator_id: int | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
    subject: str | None = None,
    tariff: str | None = None,
) -> list[ReviewItem]:
    """Всё, что сдал один ученик за период, по всем доменам сразу — карточка
    экрана «проверить всё по ученику». Непроверенные выше, внутри группы —
    свежие сверху."""
    items: list[ReviewItem] = []
    for adapter in (_task_block_items, _work_items, _homework_items, _exam_cycle_items):
        items.extend(adapter(
            db, curator_id=curator_id, student_id=student_id,
            subject=subject, tariff=tariff, week_start=week_start, week_end=week_end,
        ))
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda i: i.submitted_at or _epoch, reverse=True)
    items.sort(key=lambda i: i.is_reviewed)
    return items
