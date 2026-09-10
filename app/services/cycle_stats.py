"""Статистика прохождения цикла (владелец 03.09.2026).

«Нам нужно с каждого цикла вытаскивать вообще сколько людей там посмотрело,
сколько людей там в итоге загрузили задание… мы будем смотреть с разных
тарифов, сколько людей не досмотрело» [00:43:57–00:44:21].

Считается по тем же сущностям, что и лента: шаг цикла — блок конструктора
(`TaskBlockState`) или задание без блоков (`TrackerTaskState`). Разрез по
тарифам берётся из профиля ученика.
"""
from datetime import date

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from app.constants import TARIFFS
from app.models.learning_topic import LearningTopic
from app.models.role import Role
from app.models.task_block import TaskBlock, TaskBlockState
from app.models.tracker import STATUS_DONE, TrackerTask, TrackerTaskState
from app.models.user import User
from app.services.program import day_bounds
from app.services.tracker import cycle_bounds


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
    students = active_students(db)
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

    # «Дошли до конца» — закрыли все шаги цикла. Пустой цикл никого не считает
    # прошедшим: считать 100% там, где нечего делать, значит врать в отчёте.
    finished = 0
    if steps:
        for student in students:
            if all(
                (
                    student.id in done_by_block.get(block.id, set())
                    for blocks in blocks_by_task.values() for block in blocks
                )
            ) and all(
                student.id in done_by_task.get(task.id, set())
                for task in tasks if not blocks_by_task.get(task.id)
            ):
                finished += 1

    return {
        "topic": topic,
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
