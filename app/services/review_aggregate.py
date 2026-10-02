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
# Работы, сданные прямо в блоке задания (владелец 07.09.2026). Отдельный
# домен, а не строка внутри DOMAIN_TASK_BLOCK: там ответы на вопросы, тут
# файлы, и «проверено» у них снимается разными действиями куратора.
DOMAIN_BLOCK_WORK = "block_work"

# Домена «точка А» здесь больше нет (15.09.2026). С 09.09.2026 набор работ
# «До» показывался карточкой прямо в этом списке, и балл за него ставился
# отсюда. Теперь у точки А свой экран — `/cabinet/staff/point-a`, где рядом
# стоят ещё пять элементов входной оценки (пробники, контрольные, портфолио
# «После»), и две точки простановки одного балла были бы дублем. Сам балл
# по-прежнему пишет `student_review.py::score_portfolio_before` — эндпоинт
# остался на своём URL вместе со своими тестами, экран точки А шлёт запрос
# туда же. Подробности решения — докстринг `app/services/point_a.py`.

# С этого ранга видно всех учеников без ограничения по curator_id. Ранг 3
# (модератор) попадает под то же ограничение, что куратор — владелец про
# него не говорил (решение 1 было только про куратора), и показать меньше
# безопаснее, чем чужое (тот же приём, что student_review.py::_check_student_access).
FULL_ACCESS_RANK = 4



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
    # Куратор вернул сдачу на доработку (`homework`, `block_work`) — своя
    # пометка в списке, отдельная от is_reviewed: сдача на доработке не
    # считается проверенной, но и не должна выглядеть как обычное «на проверку».
    needs_revision: bool = False
    question: str | None = None
    chosen: list[str] | None = None
    correct: list[str] | None = None
    text: str | None = None
    review_comment: str | None = None
    # Файлы сданной работы — у `block_work`: проверяющий смотрит их прямо в
    # карточке, отдельного экрана у этого домена нет.
    images: list[str] | None = None
    # Блок «Сравнение работ»: пары, которые ученик прошёл, и работа, отмеченная
    # преподавателем, — чтобы проверяющий видел ход выбора с миниатюрами.
    compare_steps: list[dict] | None = None
    compare_pick_url: str | None = None
    # Балл 0–100 у пробника (`work`) и сдачи в задании (`block_work`). Ставит
    # только ГП (`rbac.can_score`), куратор видит его здесь, не открывая работу.
    score: int | None = None
    # Пробник: ученик сдал меньше этапных, чем задано в настройках
    # (`{"existing": 2, "required": 3}`, владелец 02.10.2026).
    stage_shortfall: dict | None = None


def _task_block_items(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
    role_rank: int = 0,
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
            compare_steps=row.get("compare_steps"),
            compare_pick_url=row.get("compare_pick_url"),
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
    role_rank: int = 0,
) -> list[ReviewItem]:
    """Пробник — не портфолио: `before`/`after` в интерфейсе никогда
    не получают `score` (нет такой формы, только галерея месяцев), включать их
    сюда значило бы навесить каждому ученику вечный «непроверено» без способа
    снять (advisor-ревью 01.09.2026). Непроверено — `score IS NULL`;
    «просмотрено» без оценки (миграция `744b7e5e4961`) тоже снимает строку с
    «непроверенных».

    Отработки здесь нет с 29.09.2026 (владелец): у ученика к ней нет входа,
    последняя сдана 13.05.2026, а ссылка «Открыть» вела на вкладку карточки,
    которой давно нет, — сервер молча открывал «Портфолио». Восемь старых
    непроверенных отработок на проде принадлежат архивным ученикам, в очередь
    они и так не попадали."""
    from app.models.user import User
    from app.models.work import WORK_TYPE_MOCK_EXAM, Work

    q = (
        db.query(Work, User)
        .join(User, User.id == Work.user_id)
        .filter(
            Work.status == "success", Work.is_final == True,  # noqa: E712
            Work.work_type == WORK_TYPE_MOCK_EXAM,
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

    from app.services.exam_cycle import stage_photo_shortfalls

    rows = q.order_by(Work.created_at.desc()).all()
    shortfalls = stage_photo_shortfalls(db, [work.cycle_id for work, _ in rows])
    items = []
    for work, student in rows:
        items.append(ReviewItem(
            domain=DOMAIN_WORK,
            item_id=work.id,
            student_id=student.id,
            title="Пробник",
            subject=work.subject,
            submitted_at=work.created_at,
            is_reviewed=work.score is not None or work.viewed_at is not None,
            review_url=f"/cabinet/students?student={student.id}&tab=mock-exams",
            score=int(work.score) if work.score is not None else None,
            stage_shortfall=shortfalls.get(work.cycle_id),
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
    role_rank: int = 0,
) -> list[ReviewItem]:
    """Сдачи домашки. Непроверено — `status != accepted`. `submitted_at` — дата
    сдачи финального фото, не дедлайн постановки (`TrackerTask.due_at`)."""
    from app.models.homework_submission import (
        STATUS_ACCEPTED,
        STATUS_NEEDS_REVISION,
        HomeworkSubmission,
    )
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
            needs_revision=submission.status == STATUS_NEEDS_REVISION,
        ))
    return items



def _block_work_items(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
    role_rank: int = 0,
) -> list[ReviewItem]:
    """Работы, сданные в блоке задания — обёртка над
    `task_blocks.py::submission_review_queue`.

    Отдельного *списка* сдач у них нет и заводить его нельзя (инвариант
    проекта: новый тип сдачи получает адаптер, а не свой роут и пункт меню) —
    сдачи видны в том же общем списке на `/cabinet/staff/students-review/{id}`,
    что и всё остальное по ученику. Сам диалог с оценкой при этом, как и у
    домашки (`_homework_items`) и пробника (`_exam_cycle_items`), живёт на
    своей странице — `review_url` ведёт в `task_block_feedback.py`
    (`/cabinet/staff/task-block-submissions/{id}/feedback`).
    """
    from app.services.task_blocks import submission_review_queue

    raw = submission_review_queue(
        db,
        curator_id=curator_id,
        student_id=student_id,
        subject=subject,
        tariff=tariff,
        week_start=week_start,
        week_end=week_end,
        limit=100_000,
    )
    items = []
    for row in raw:
        title = row["task_title"]
        if row["block_title"]:
            title = f"{title} — {row['block_title']}"
        if row["overrun"]:
            title = f"{title} (время превышено)"
        if row["late"]:
            title = f"{title} (сдано после срока)"
        items.append(ReviewItem(
            domain=DOMAIN_BLOCK_WORK,
            item_id=row["submission_id"],
            student_id=row["student_id"],
            title=title,
            subject=row["subject"],
            submitted_at=row["submitted_at"],
            is_reviewed=row["reviewed"],
            review_url=f"/cabinet/staff/task-block-submissions/{row['submission_id']}/feedback",
            text=row["comment"],
            review_comment=row["review_comment"],
            images=row["images"],
            needs_revision=row["needs_revision"],
            score=row["score"],
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
    role_rank: int = 0,
) -> list[ReviewItem]:
    """Циклы Пробника. Непроверено — открыт (`closed_at IS NULL`) либо «на
    правке» (`revision_requested_at` без `revision_done_at`); «просмотрено» без
    закрытия (миграция `744b7e5e4961`) тоже снимает строку с «непроверенных».

    `review_url` ведёт прямо в диалог цикла (`cabinet_feedback_detail.html`),
    а не на JSON-список `/cabinet/students/{id}/cycles` — тот эндпоинт отдаёт
    сырые данные, а не HTML (починка 02.09.2026). Префикс роута по рангу —
    тот же трёхветочный выбор, что уже дублируется в `app/api/feedback.py`
    (`staff_cycles_list`, `student_cycles_json`)."""
    from app.models.exam_cycle import ExamCycle
    from app.models.user import User

    if role_rank >= 5:
        detail_prefix = "/cabinet/superadmin/feedback/"
    elif role_rank >= 4:
        detail_prefix = "/cabinet/admin/feedback/"
    else:
        detail_prefix = "/cabinet/curator/feedback/"

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
            review_url=f"{detail_prefix}{cycle.id}",
        ))
    return items


# --- список учеников со счётчиками и сортировкой (этап 4) -------------------


def _accessible_students(db: DBSession, user: dict) -> list:
    """Канонический список — `_get_accessible_students` (решение владельца,
    вопрос 2): куратор (и модератор, `FULL_ACCESS_RANK`) видит `curator_id` +
    `is_active`, admin+ — всех активных."""
    from app.constants import REPORT_EXCLUDED_USER_IDS
    from app.models.role import Role
    from app.models.user import User

    if user["role_rank"] < FULL_ACCESS_RANK:
        return (
            db.query(User)
            .filter(
                User.curator_id == user["user_id"],
                User.is_active == True,  # noqa: E712
                User.id.notin_(REPORT_EXCLUDED_USER_IDS),
            )
            .order_by(User.last_name, User.first_name)
            .all()
        )
    student_role = db.query(Role).filter(Role.rank == 1).first()
    if not student_role:
        return []
    return (
        db.query(User)
        .filter(
            User.role_id == student_role.id,
            User.is_active == True,  # noqa: E712
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),
        )
        .order_by(User.last_name, User.first_name)
        .all()
    )


def _unreviewed_counts_by_student(
    db: DBSession, *, curator_id: int | None, role_rank: int = 0
) -> dict[int, int]:
    """Счётчик непроверенного по каждому ученику, сложенный по всем доменам.

    Переиспользует адаптеры, а не отдельные COUNT-запросы: список учеников на
    экран проверки небольшой (школа, не тысячи учеников), а адаптеры и так уже
    написаны и протестированы — второй параллельный набор запросов ради
    счётчика того не стоит.

    `role_rank` прокидывается в адаптеры: по нему цикл Пробника выбирает, на
    какой из трёх диалогов вести ссылку (`/cabinet/curator|admin|superadmin`).
    """
    from collections import Counter

    counts: Counter[int] = Counter()
    for adapter in (
        _task_block_items, _work_items, _homework_items, _exam_cycle_items,
        _block_work_items,
    ):
        for item in adapter(db, curator_id=curator_id, role_rank=role_rank):
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
    counts = _unreviewed_counts_by_student(
        db, curator_id=curator_id, role_rank=user.get("role_rank", 0)
    )

    rows = [
        {"student": student, "unchecked": counts.get(student.id, 0)}
        for student in students
    ]
    rows.sort(key=lambda row: (-row["unchecked"], (row["student"].last_name or ""), (row["student"].first_name or row["student"].name or "")))
    return rows


# --- поиск и фильтры списка (владелец 28.09.2026) ----------------------------

# Значения фильтров из GET-формы экрана. `NEWCOMER_TARIFF` — тот же маркер, что
# у пилюли «Новенький» на странице «Ученики»: «без тарифа» в базе хранится
# пустой строкой, а пустое значение формы уже значит «любой тариф».
REVIEW_STATUS_UNCHECKED = "unchecked"
REVIEW_STATUS_CHECKED = "checked"
NEWCOMER_TARIFF = "__newcomer__"
NO_CURATOR = "none"


def normalize_student_search(value: object) -> str:
    """Python-двойник `normalizeStudentSearch` из `cabinet_students.html`:
    NFKC, регистр, ё→е, схлопнутые пробелы. Правила общие, чтобы один и тот же
    запрос находил одних и тех же учеников на обоих экранах."""
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value or "")).lower().replace("ё", "е")
    return " ".join(text.split())


def filter_review_rows(
    rows: list[dict],
    *,
    q: str = "",
    status: str = "",
    tariff: str = "",
    curator: str = "",
    search_contacts: bool = False,
) -> list[dict]:
    """Сужает список `aggregate_student_review_counts` — только сужает.

    Круг учеников задаёт `_accessible_students`, фильтр по куратору здесь лишь
    выбирает из уже доступных, поэтому куратор не расширит выборку, подставив
    чужой id в адрес. Поиск — каждое слово запроса должно найтись в имени (или
    в @username / VK ID при `search_contacts`, как и на «Учениках»: контакты
    там видит только ГП и выше).
    """
    tokens = [t.lstrip("@") for t in normalize_student_search(q).split(" ") if t.lstrip("@")]

    result = []
    for row in rows:
        student = row["student"]
        if status == REVIEW_STATUS_UNCHECKED and row["unchecked"] <= 0:
            continue
        if status == REVIEW_STATUS_CHECKED and row["unchecked"] > 0:
            continue
        if tariff == NEWCOMER_TARIFF:
            if (student.tariff or "").strip():
                continue
        elif tariff and student.tariff != tariff:
            continue
        if curator == NO_CURATOR:
            if student.curator_id is not None:
                continue
        elif curator.isdigit() and student.curator_id != int(curator):
            continue
        if tokens:
            parts = [student.last_name, student.first_name, student.name]
            if search_contacts:
                # vk_id — внутренний номер, людям не показывается (VK удалён
                # 29.09.2026), искать по нему некому.
                parts.append((student.tg_username or "").lstrip("@"))
            haystack = normalize_student_search(" ".join(str(p) for p in parts if p))
            if not all(token in haystack for token in tokens):
                continue
        result.append(row)
    return result


def review_curator_options(db: DBSession, rows: list[dict]) -> list[dict]:
    """Кураторы для выпадающего списка — те, за кем числятся ученики списка.

    Не выборка «все с рангом 2»: ученик может быть закреплён и за модератором,
    а куратор без учеников в фильтре дал бы только пустой экран."""
    from app.models.user import User

    ids = {row["student"].curator_id for row in rows if row["student"].curator_id}
    if not ids:
        return []
    staff = (
        db.query(User)
        .filter(User.id.in_(ids))
        .order_by(User.last_name, User.first_name)
        .all()
    )
    return [
        {"id": s.id, "name": f"{s.last_name or ''} {s.first_name or s.name}".strip()}
        for s in staff
    ]


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
    role_rank: int = 0,
) -> list[ReviewItem]:
    """Всё, что сдал один ученик за период, по всем доменам сразу — карточка
    экрана «проверить всё по ученику». Непроверенные выше, внутри группы —
    свежие сверху."""
    items: list[ReviewItem] = []
    for adapter in (
        _task_block_items, _work_items, _homework_items, _exam_cycle_items,
        _block_work_items,
    ):
        items.extend(adapter(
            db, curator_id=curator_id, student_id=student_id,
            subject=subject, tariff=tariff, week_start=week_start, week_end=week_end,
            role_rank=role_rank,
        ))
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda i: i.submitted_at or _epoch, reverse=True)
    items.sort(key=lambda i: i.is_reviewed)
    return items
