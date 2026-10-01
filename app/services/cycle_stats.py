"""Статистика прохождения цикла (владелец 03.09.2026).

«Нам нужно с каждого цикла вытаскивать вообще сколько людей там посмотрело,
сколько людей там в итоге загрузили задание… мы будем смотреть с разных
тарифов, сколько людей не досмотрело» [00:43:57–00:44:21].

Считается по тем же сущностям, что и лента: шаг цикла — блок конструктора
(`TaskBlockState`) или задание без блоков (`TrackerTaskState`). Разрез по
тарифам берётся из профиля ученика.
"""
from datetime import date, datetime, timezone

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from app.cache import invalidate_unread
from app.constants import REPORT_EXCLUDED_USER_IDS, TARIFFS
from app.models.learning_topic import LearningTopic
from app.models.notification import Notification
from app.models.role import Role
from app.models.student_reminder import KIND_CYCLE_DEBT, StudentReminder
from app.models.task_block import TaskBlock, TaskBlockState
from app.models.tracker import STATUS_DONE, TrackerTask, TrackerTaskState
from app.models.user import User
from app.services.program import day_bounds, msk_date
from app.services.tracker import cycle_bounds, cycle_label, missing_required_tasks
from app.services.video_topics import saw_topic_period


def active_students(db: Session) -> list[User]:
    """Ученики, которых имеет смысл считать: активные, не удалённые, не в архиве.

    Тот же отбор, что в `contacts.py` — второго определения «действующего
    ученика» в проекте заводить не нужно.
    """
    return (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),
        )
        .all()
    )


def _cycle_tasks(db: Session, topic_id: int, first: date, last: date) -> list[TrackerTask]:
    """Задачи цикла: свои по `topic_id` (новая схема, заданы без даты) плюс
    старые датные, попавшие в период по `due_at` (совместимость со старыми
    циклами, где `topic_id` задачи — служебная тема элемента дня, не сам
    цикл). Пересечения множеств нет: старые датные элементы никогда не имеют
    `topic_id == topic_id`, у них своя одноразовая тема."""
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    no_due_last = case((TrackerTask.due_at.is_(None), 1), else_=0)
    return (
        db.query(TrackerTask)
        .filter(
            TrackerTask.is_published.is_(True),
            TrackerTask.deleted_at.is_(None),
            or_(
                TrackerTask.topic_id == topic_id,
                and_(TrackerTask.due_at >= start, TrackerTask.due_at < end),
            ),
        )
        .order_by(no_due_last, TrackerTask.due_at, TrackerTask.sort_order, TrackerTask.id)
        .all()
    )


def cycle_stats(db: Session, topic: LearningTopic) -> dict:
    """Прохождение цикла: по каждому шагу — сколько учеников закрыли, и разрез
    по тарифам.

    Шаг — блок конструктора; задание без блоков считается одним шагом, как и в
    ленте (`cycle_feed.py`), иначе экран статистики и экран ученика разошлись
    бы в том, что вообще считать заданием.
    """
    first, last = cycle_bounds(topic)
    tasks = _cycle_tasks(db, topic.id, first, last)
    students = _audience(db, topic)
    total = len(students)
    tariff_of = {student.id: (student.tariff or "").strip().upper() for student in students}
    student_ids = set(tariff_of)

    blocks_by_task: dict[int, list[TaskBlock]] = {}
    if tasks:
        rows = (
            db.query(TaskBlock)
            .filter(TaskBlock.task_id.in_([t.id for t in tasks]))
            .order_by(TaskBlock.task_id, TaskBlock.sort_order, TaskBlock.id)
            .all()
        )
        for block in rows:
            blocks_by_task.setdefault(block.task_id, []).append(block)

    block_ids = [b.id for blocks in blocks_by_task.values() for b in blocks]
    done_by_block: dict[int, set[int]] = {}
    if block_ids:
        for state in (
            db.query(TaskBlockState)
            .filter(
                TaskBlockState.block_id.in_(block_ids),
                TaskBlockState.status == STATUS_DONE,
            )
            .all()
        ):
            if state.user_id in student_ids:
                done_by_block.setdefault(state.block_id, set()).add(state.user_id)

    done_by_task: dict[int, set[int]] = {}
    if tasks:
        for state in (
            db.query(TrackerTaskState)
            .filter(
                TrackerTaskState.task_id.in_([t.id for t in tasks]),
                TrackerTaskState.status == STATUS_DONE,
            )
            .all()
        ):
            if state.user_id in student_ids:
                done_by_task.setdefault(state.task_id, set()).add(state.user_id)

    def by_tariff(done_ids: set[int]) -> dict[str, int]:
        counts = {tariff: 0 for tariff in TARIFFS}
        for user_id in done_ids:
            tariff = tariff_of.get(user_id)
            if tariff in counts:
                counts[tariff] += 1
        return counts

    steps = []
    for task in tasks:
        task_blocks = blocks_by_task.get(task.id, [])
        if not task_blocks:
            done_ids = done_by_task.get(task.id, set())
            steps.append({
                "title": task.title,
                "kind": "task",
                "done": len(done_ids),
                "total": total,
                "by_tariff": by_tariff(done_ids),
            })
            continue
        for block in task_blocks:
            done_ids = done_by_block.get(block.id, set())
            steps.append({
                "title": block.title or task.title,
                "kind": block.block_type,
                "done": len(done_ids),
                "total": total,
                "by_tariff": by_tariff(done_ids),
            })

    # «Прошли цикл целиком» — по правилу ленты ученика (`is_cycle_complete`),
    # а не «закрыл все блоки». До 30.09.2026 считалось по блокам, и ученик
    # «Я С ВАМИ» не мог пройти цикл с роликом тарифа «Уверенный максимум»,
    # которого он даже не видит, — цифра врала вниз. Пустой цикл никого не
    # считает прошедшим: 100% там, где нечего делать, — тоже неправда.
    finished = 0
    if steps:
        missing = _missing_by_student(db, topic, students)
        finished = sum(1 for student in students if not missing[student.id])

    return {
        "topic": topic,
        # Подпись, а не сырой `title`: название цикла необязательно с
        # 10.09.2026, и у безымянного экран статистики показывал пустой
        # заголовок. `cycle_label` в этом случае отдаёт период датами — так же,
        # как список циклов и переключатель в ленте ученика.
        "label": cycle_label(db, topic),
        "start": first,
        "end": last,
        "students": total,
        "steps": steps,
        "finished": finished,
        "tariffs": TARIFFS,
        "students_by_tariff": {
            tariff: sum(1 for value in tariff_of.values() if value == tariff)
            for tariff in TARIFFS
        },
    }


def _audience(db: Session, topic: LearningTopic) -> list[User]:
    """Ученики, которым цикл был виден. Пришедший после его конца цикла не
    видит (владелец 29.09.2026) — и в «не сдали» его считать нельзя."""
    return [
        student for student in active_students(db)
        if saw_topic_period(student.program_access_from, topic)
    ]


def _missing_by_student(
    db: Session, topic: LearningTopic, students: list[User]
) -> dict[int, list[TrackerTask]]:
    """Незакрытые обязательные задачи цикла у каждого ученика — по тому же
    правилу, что гейт ленты (`tracker.missing_required_tasks`)."""
    return {
        student.id: missing_required_tasks(db, student.id, topic)
        for student in students
    }


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reminder_ref(topic: LearningTopic, now: datetime) -> str:
    return f"{topic.id}:{msk_date(now).isoformat()}"


def cycle_debtors(
    db: Session, topic: LearningTopic, now: datetime | None = None
) -> list[dict]:
    """Кто цикл не закрыл (владелец 30.09.2026): ученик, задачи, которых не
    хватает, и напоминали ли ему сегодня.

    Отбор — те же ученики, что в статистике, минус истёкший доступ: новенький
    после пробного периода заблокирован, писать ему «закрой цикл» бессмысленно.
    Пустой цикл должников не имеет — как и прошедших (см. `cycle_stats`).
    """
    now = _utc(now) or datetime.now(timezone.utc)
    first, last = cycle_bounds(topic)
    if not _cycle_tasks(db, topic.id, first, last):
        return []
    students = [
        student for student in _audience(db, topic)
        if not (student.access_until is not None and _utc(student.access_until) <= now)
    ]
    missing = _missing_by_student(db, topic, students)
    debtors = [student for student in students if missing[student.id]]
    reminded: set[int] = set()
    if debtors:
        reminded = {
            row[0] for row in db.query(StudentReminder.user_id).filter(
                StudentReminder.kind == KIND_CYCLE_DEBT,
                StudentReminder.ref == _reminder_ref(topic, now),
                StudentReminder.user_id.in_([s.id for s in debtors]),
            )
        }
    debtors.sort(key=lambda s: (
        (s.tariff or "").strip().upper(),
        (s.last_name or s.name or "").lower(),
        (s.first_name or "").lower(),
    ))
    return [
        {
            "user": student,
            "tasks": missing[student.id],
            "reminded_today": student.id in reminded,
        }
        for student in debtors
    ]


def reminder_message(label: str, tasks: list[TrackerTask]) -> tuple[str, str]:
    """Заголовок и текст напоминания должнику. Кнопка «Завершить задание» в
    тексте не случайна: 30.09.2026 треть группы застряла, отметив кружками все
    ролики, но не нажав её. С 01.10.2026 у задания, которое закрывается само,
    кнопки нет (`task_blocks.completion_button_needed`) — отсюда «если есть»."""
    title = (
        f"{label} не закрыт" if label.lower().startswith("цикл")
        else f"Цикл «{label}» не закрыт"
    )
    names = ", ".join(f"«{task.title}»" for task in tasks)
    if len(tasks) == 1:
        text = (
            f"Осталось задание {names}. Открой его в «Обучении» и пройди до конца. "
            "Если внизу есть кнопка «Завершить задание», нажми её."
        )
    else:
        text = (
            f"Осталось заданий: {len(tasks)} – {names}. Открой их в «Обучении» и пройди "
            "каждое до конца. Если внизу есть кнопка «Завершить задание», нажми её."
        )
    return title, text


def remind_cycle_debtors(
    db: Session, topic: LearningTopic, now: datetime | None = None
) -> tuple[list[int], int]:
    """Создать напоминания должникам цикла. Коммитит сам.

    Возвращает id уведомлений и сколько должников пропущено, потому что им
    сегодня уже напоминали. Рассылку в Telegram и push делает вызывающий после
    коммита — **по одному** (`notify`), не пачкой `notify_many`: 30
    параллельных отправок 30.09.2026 выбрали пул соединений базы.
    """
    now = _utc(now) or datetime.now(timezone.utc)
    label = cycle_label(db, topic)
    ref = _reminder_ref(topic, now)
    created: list[Notification] = []
    skipped = 0
    for debtor in cycle_debtors(db, topic, now):
        if debtor["reminded_today"]:
            skipped += 1
            continue
        student = debtor["user"]
        title, text = reminder_message(label, debtor["tasks"])
        notification = Notification(user_id=student.id, title=title[:200], text=text)
        db.add(notification)
        db.add(StudentReminder(user_id=student.id, kind=KIND_CYCLE_DEBT, ref=ref))
        created.append(notification)
    db.commit()
    for notification in created:
        invalidate_unread(notification.user_id)
    return [notification.id for notification in created], skipped
