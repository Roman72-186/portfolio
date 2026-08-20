"""Задачи «Личного трекера»: адресация, охват и управление.

Source of truth по тому, кому адресована задача и кто её увидит. Адресация
скопирована с тем видеоуроков (`video_topics.py`): явный флаг «всем», теги,
поимённые исключения. **Сопоставление тегов строгое, по `tag_id`** — эвристика
предметов из пробников («Р» и «К» как маркеры предмета) в проде означает группу
и уровень куратора и уже прятала билеты от учеников (`TODO.md` §7).

Разбор решений владельца — plans/2026-08-20-apparchi-tracker-and-digest.md.
"""

from datetime import datetime, timezone

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.models.role import Role
from app.models.tag import UserTag
from app.models.tracker import (
    STATUS_DONE,
    TrackerTask,
    TrackerTaskAssignee,
    TrackerTaskState,
    TrackerTaskTag,
)
from app.models.user import User
from app.services.tags import parse_usernames

STUDENT_ROLE_RANK = 1


def list_tasks(db: Session, *, include_deleted: bool = False) -> list[TrackerTask]:
    """Задачи для списка преподавателя: ближайший дедлайн сверху.

    Порядок задан явным `case`, а не «`due_at asc`»: SQLite ставит NULL в начало
    возрастающей сортировки, Postgres — в конец, и список в тестах разошёлся бы
    с продом. Задачи без дедлайна всегда внизу.
    """
    query = db.query(TrackerTask)
    if not include_deleted:
        query = query.filter(TrackerTask.deleted_at.is_(None))
    no_due_last = case((TrackerTask.due_at.is_(None), 1), else_=0)
    return query.order_by(
        no_due_last, TrackerTask.due_at.asc(), TrackerTask.id.desc()
    ).all()


def get_task(db: Session, task_id: int) -> TrackerTask | None:
    task = db.get(TrackerTask, task_id)
    if task is None or task.deleted_at is not None:
        return None
    return task


def create_task(
    db: Session,
    *,
    title: str,
    user_id: int,
    description: str | None = None,
    due_at: datetime | None = None,
    starts_at: datetime | None = None,
    subject: str | None = None,
    assign_to_all: bool = False,
) -> TrackerTask:
    task = TrackerTask(
        title=title,
        description=description,
        due_at=due_at,
        starts_at=starts_at,
        subject=subject,
        assign_to_all=assign_to_all,
        created_by_id=user_id,
    )
    db.add(task)
    db.flush()
    return task


def update_task(
    task: TrackerTask,
    *,
    title: str,
    description: str | None = None,
    due_at: datetime | None = None,
    starts_at: datetime | None = None,
    subject: str | None = None,
    assign_to_all: bool = False,
) -> None:
    task.title = title
    task.description = description
    task.due_at = due_at
    task.starts_at = starts_at
    task.subject = subject
    task.assign_to_all = assign_to_all


def set_task_tags(db: Session, task: TrackerTask, tag_ids: list[int]) -> None:
    """Переписать адресацию по тегам целиком."""
    db.query(TrackerTaskTag).filter(TrackerTaskTag.task_id == task.id).delete(
        synchronize_session=False
    )
    for tag_id in dict.fromkeys(tag_ids):
        db.add(TrackerTaskTag(task_id=task.id, tag_id=tag_id))
    db.flush()


def set_task_assignees(db: Session, task: TrackerTask, user_ids: list[int]) -> None:
    """Переписать поимённые исключения целиком."""
    db.query(TrackerTaskAssignee).filter(
        TrackerTaskAssignee.task_id == task.id
    ).delete(synchronize_session=False)
    for user_id in dict.fromkeys(user_ids):
        db.add(TrackerTaskAssignee(task_id=task.id, user_id=user_id))
    db.flush()


def get_tag_ids(db: Session, task_id: int) -> list[int]:
    rows = (
        db.query(TrackerTaskTag.tag_id)
        .filter(TrackerTaskTag.task_id == task_id)
        .all()
    )
    return [row[0] for row in rows]


def get_assignee_ids(db: Session, task_id: int) -> list[int]:
    rows = (
        db.query(TrackerTaskAssignee.user_id)
        .filter(TrackerTaskAssignee.task_id == task_id)
        .all()
    )
    return [row[0] for row in rows]


def resolve_assignees(db: Session, raw: str) -> tuple[list[int], list[str]]:
    """@username → id учеников. Возвращает найденных и ненайденных.

    `tg_username` зашифрован (EncryptedString), поэтому сравниваем в Python после
    расшифровки, а не через SQL. Логика повторяет `video_admin._resolve_assignees`
    намеренно: тот файл ведёт вторая сессия и лежит под своим набором тестов,
    и общий хелпер связал бы два модуля ради двух десятков строк.
    """
    requested = parse_usernames(raw)
    if not requested:
        return [], []
    wanted = set(requested)
    student_role = db.query(Role).filter(Role.rank == STUDENT_ROLE_RANK).first()
    if student_role is None:
        return [], requested

    found: dict[str, int] = {}
    candidates = (
        db.query(User)
        .filter(
            User.role_id == student_role.id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .all()
    )
    for candidate in candidates:
        uname = (candidate.tg_username or "").strip().lstrip("@").lower()
        if uname in wanted and uname not in found:
            found[uname] = candidate.id
    not_found = [u for u in requested if u not in found]
    return list(found.values()), not_found


def assignee_usernames(db: Session, user_ids: list[int]) -> str:
    """id учеников → строка «@user1, @user2» для предзаполнения формы.

    Без неё форма правки открывалась бы с пустым полем, а сохранение переписывает
    список поимённых целиком — то есть любая правка задачи снимала бы её у всех,
    кого добавили сверх тегов. На темах видеоуроков это уже случилось.
    """
    if not user_ids:
        return ""
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    by_id = {u.id: (u.tg_username or "").strip().lstrip("@") for u in users}
    names = [by_id.get(uid, "") for uid in user_ids]
    return ", ".join(f"@{name}" for name in names if name)


def count_task_audience(
    db: Session,
    *,
    assign_to_all: bool,
    tag_ids: list[int],
    assignee_ids: list[int],
) -> int:
    """Сколько активных учеников реально получат задачу.

    Проверка на глаз перед публикацией: адресовал тегу «Р», увидел троих вместо
    сорока — выбран не тот тег.

    Членство в группе здесь **не учитывается**, в отличие от `count_topic_audience`
    видеомодуля. Это не пропущенный фильтр: экран `/cabinet/tracker` закрыт
    `require_student` (ранг >= 1), а видеомодуль — `require_learning_content_access`,
    который заворачивает учеников вне группы. Добавить сюда `is_group_member`
    значило бы занизить охват и напугать преподавателя нулём там, где задачу
    увидят.
    """
    students = (
        db.query(User.id)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == STUDENT_ROLE_RANK,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if assign_to_all:
        return students.count()

    reached: set[int] = set()
    if tag_ids:
        rows = (
            students.join(UserTag, UserTag.user_id == User.id)
            .filter(UserTag.tag_id.in_(tag_ids))
            .all()
        )
        reached.update(row[0] for row in rows)
    if assignee_ids:
        rows = students.filter(User.id.in_(assignee_ids)).all()
        reached.update(row[0] for row in rows)
    return len(reached)


def count_completed(db: Session, task_id: int) -> int:
    """Сколько учеников уже закрыли задачу. Строка состояния заводится лениво,
    поэтому «нет строки» — это открытая задача, а не отсутствие адресата."""
    return (
        db.query(TrackerTaskState.id)
        .filter(
            TrackerTaskState.task_id == task_id,
            TrackerTaskState.status == STATUS_DONE,
        )
        .count()
    )


def publish_task(task: TrackerTask, *, user_id: int) -> None:
    if task.deleted_at is not None:
        raise ValueError("Task is deleted")
    task.is_published = True
    task.published_at = datetime.now(timezone.utc)
    task.published_by_id = user_id


def unpublish_task(task: TrackerTask) -> None:
    task.is_published = False
    task.published_at = None
    task.published_by_id = None


def delete_task(task: TrackerTask) -> None:
    """Мягкое удаление: строки состояния учеников остаются, задача пропадает из
    выдачи по `deleted_at`. Физического удаления нет, отчётность не теряется."""
    task.deleted_at = datetime.now(timezone.utc)
    task.is_published = False
