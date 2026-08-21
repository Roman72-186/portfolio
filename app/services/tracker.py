"""Задачи «Личного трекера»: адресация, охват и управление.

Source of truth по тому, кому адресована задача и кто её увидит. Адресация
скопирована с тем видеоуроков (`video_topics.py`): явный флаг «всем», теги,
поимённые исключения. **Сопоставление тегов строгое, по `tag_id`** — эвристика
предметов из пробников («Р» и «К» как маркеры предмета) в проде означает группу
и уровень куратора и уже прятала билеты от учеников (`TODO.md` §7).

Разбор решений владельца — plans/2026-08-20-apparchi-tracker-and-digest.md.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app.models.homework import HomeworkAssignment, HomeworkImage
from app.models.learning_topic import LearningTopic
from app.models.role import Role
from app.models.tag import UserTag
from app.models.tracker import (
    ITEM_KIND_LABELS,
    ITEM_OTHER,
    SOURCE_HOMEWORK,
    SOURCE_LEARNING_TOPIC,
    STATUS_DONE,
    STATUS_OPEN,
    TrackerTask,
    TrackerTaskAssignee,
    TrackerTaskState,
    TrackerTaskTag,
)
from app.models.user import User
from app.services.program import msk_date
from app.services.tags import parse_usernames
from app.services.tz import MSK_TZ, now_msk
# Неделя программы — это тема недели видеомодуля: расписание, публикация и
# адресация там уже обкатаны тестами. Второй сущности «неделя» не заводим,
# поэтому управление темой берём из её владельца, а не дублируем здесь.
from app.services.video_topics import (
    accessible_topic_ids,
    count_topic_audience,
    create_topic,
    get_assignee_ids as topic_assignee_ids,
    get_tag_ids as topic_tag_ids,
    set_topic_assignees,
    set_topic_tags,
)

STUDENT_ROLE_RANK = 1


def list_tasks(
    db: Session, *, include_deleted: bool = False, standalone_only: bool = False
) -> list[TrackerTask]:
    """Задачи для списка преподавателя: ближайший дедлайн сверху.

    `standalone_only` оставляет только разовые задачи вне программы: элементы
    недель живут на своём экране, и без фильтра одна и та же строка показывалась
    бы на двух экранах сразу.

    Порядок задан явным `case`, а не «`due_at asc`»: SQLite ставит NULL в начало
    возрастающей сортировки, Postgres — в конец, и список в тестах разошёлся бы
    с продом. Задачи без дедлайна всегда внизу.
    """
    query = db.query(TrackerTask)
    if not include_deleted:
        query = query.filter(TrackerTask.deleted_at.is_(None))
    if standalone_only:
        query = query.filter(TrackerTask.topic_id.is_(None))
    no_due_last = case((TrackerTask.due_at.is_(None), 1), else_=0)
    return query.order_by(
        no_due_last, TrackerTask.due_at.asc(), TrackerTask.id.desc()
    ).all()


def list_week_items(db: Session, topic_id: int) -> list[TrackerTask]:
    """Элементы одной недели по порядку: сначала по дате, внутри дня — по sort_order."""
    no_due_last = case((TrackerTask.due_at.is_(None), 1), else_=0)
    return (
        db.query(TrackerTask)
        .filter(TrackerTask.topic_id == topic_id, TrackerTask.deleted_at.is_(None))
        .order_by(
            no_due_last,
            TrackerTask.due_at.asc(),
            TrackerTask.sort_order.asc(),
            TrackerTask.id.asc(),
        )
        .all()
    )


def count_week_items(db: Session, topic_id: int) -> int:
    return (
        db.query(TrackerTask.id)
        .filter(TrackerTask.topic_id == topic_id, TrackerTask.deleted_at.is_(None))
        .count()
    )


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
    topic_id: int | None = None,
    kind: str = ITEM_OTHER,
    sort_order: int = 0,
    source_kind: str | None = None,
    source_id: int | None = None,
) -> TrackerTask:
    task = TrackerTask(
        title=title,
        description=description,
        due_at=due_at,
        starts_at=starts_at,
        subject=subject,
        assign_to_all=assign_to_all,
        topic_id=topic_id,
        kind=kind,
        sort_order=sort_order,
        source_kind=source_kind,
        source_id=source_id,
        created_by_id=user_id,
    )
    db.add(task)
    db.flush()
    return task


def update_task(
    task: TrackerTask,
    *,
    # Неделя у элемента не меняется: перенос между неделями не требовался, а
    # молчаливая смена topic_id увела бы задачу к другой аудитории.
    title: str,
    description: str | None = None,
    due_at: datetime | None = None,
    starts_at: datetime | None = None,
    subject: str | None = None,
    assign_to_all: bool = False,
    kind: str | None = None,
    sort_order: int | None = None,
) -> None:
    task.title = title
    task.description = description
    task.due_at = due_at
    task.starts_at = starts_at
    task.subject = subject
    task.assign_to_all = assign_to_all
    if kind is not None:
        task.kind = kind
    if sort_order is not None:
        task.sort_order = sort_order


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
    """Сколько активных учеников реально получат разовую задачу.

    Проверка на глаз перед публикацией: адресовал тегу «Р», увидел троих вместо
    сорока — выбран не тот тег.

    **Только для задач вне программы.** У элемента недели своей адресации нет,
    его охват считает `week_audience()` по неделе — эта функция вернула бы ноль,
    потому что у элемента пустые теги.

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


def accessible_task_ids(db: Session, user_id: int) -> set[int]:
    """Разовые задачи, открытые ученику прямо сейчас — зеркало
    `accessible_topic_ids` из video_topics.py, но по TrackerTaskTag/
    TrackerTaskAssignee. Задача открыта, если опубликована, не удалена и
    адресована ученику: флагом «всем», пересечением тегов или поимённо.

    Только для задач вне программы (`topic_id IS NULL`) — у элемента недели
    своей адресации нет, его аудиторию считает `week_audience()` по неделе.
    Временных границ здесь нет: их накладывает вызывающий код по due_at.
    """
    user_tag_ids = (
        db.query(UserTag.tag_id).filter(UserTag.user_id == user_id).scalar_subquery()
    )
    tagged_task_ids = (
        db.query(TrackerTaskTag.task_id)
        .filter(TrackerTaskTag.tag_id.in_(user_tag_ids))
        .scalar_subquery()
    )
    assigned_task_ids = (
        db.query(TrackerTaskAssignee.task_id)
        .filter(TrackerTaskAssignee.user_id == user_id)
        .scalar_subquery()
    )
    rows = (
        db.query(TrackerTask.id)
        .filter(
            TrackerTask.deleted_at.is_(None),
            TrackerTask.is_published.is_(True),
            or_(
                TrackerTask.assign_to_all.is_(True),
                TrackerTask.id.in_(tagged_task_ids),
                TrackerTask.id.in_(assigned_task_ids),
            ),
        )
        .all()
    )
    return {row[0] for row in rows}


def task_status(
    task: TrackerTask, state: TrackerTaskState | None, *, now: datetime
) -> Literal["done", "overdue", "upcoming"]:
    """Цвет строки у ученика: сделано → просрочено → есть время.

    Чистая функция без обращения к БД — состояние передаётся уже прочитанным,
    `now` передаётся явно (через `app/services/tz.py::now_msk()` у вызывающего
    кода), чтобы не путать колонку без дедлайна с просроченной: задача без
    `due_at` считается «есть время», пока её не закрыли.
    """
    if state is not None and state.status == STATUS_DONE:
        return "done"
    due_at = task.due_at
    if due_at is not None:
        # SQLite в тестах отдаёт наивное время; весь проект трактует такое
        # значение как UTC (см. program.py::msk_date) — иначе naive < aware
        # уронет сравнение TypeError'ом на движке с TIMESTAMPTZ.
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        if due_at < now:
            return "overdue"
    return "upcoming"


def accessible_task_entries(
    db: Session, user_id: int, *, start: datetime | None, end: datetime
) -> list[dict]:
    """Задачи ученика (программа + разовые) до `end` с личным статусом.

    Общий движок для двух экранов, которые раньше собирали один и тот же
    запрос по отдельности: «Личный трекер» зовёт с `start=None` — долг
    копится, пока не закрыт, недели назад он никуда не девается; «Актуальное
    образовательное пространство» передаёт границы текущей недели, для
    разбивки по дням. Каждая запись — словарь с `task`/`kind_label`/`status`/
    `due_label`/`day`, без похода в шаблон за вычислениями.
    """
    topic_ids = accessible_topic_ids(db, user_id)
    task_ids = accessible_task_ids(db, user_id)
    filters = [
        TrackerTask.is_published.is_(True),
        TrackerTask.deleted_at.is_(None),
        TrackerTask.due_at < end,
        or_(
            TrackerTask.topic_id.in_(topic_ids),
            TrackerTask.topic_id.is_(None) & TrackerTask.id.in_(task_ids),
        ),
    ]
    if start is not None:
        filters.insert(2, TrackerTask.due_at >= start)
    tasks = (
        db.query(TrackerTask)
        .filter(*filters)
        .order_by(TrackerTask.due_at.asc(), TrackerTask.sort_order.asc(), TrackerTask.id.asc())
        .all()
    )

    states: dict[int, TrackerTaskState] = {}
    if tasks:
        rows = (
            db.query(TrackerTaskState)
            .filter(
                TrackerTaskState.task_id.in_([t.id for t in tasks]),
                TrackerTaskState.user_id == user_id,
            )
            .all()
        )
        states = {row.task_id: row for row in rows}

    now = now_msk()
    entries = []
    for task in tasks:
        due_at = task.due_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        state = states.get(task.id)
        entries.append({
            "task": task,
            "kind_label": ITEM_KIND_LABELS.get(task.kind, task.kind),
            "status": task_status(task, state, now=now),
            "due_label": due_at.astimezone(MSK_TZ).strftime("%H:%M"),
            "day": msk_date(task.due_at),
            # Дата закрытия отдельно от `day` (дата дедлайна): «Сделано» на
            # экране ученика фильтруется по ней, иначе закрытый долг прошлой
            # недели пропадал с экрана — из «Просрочено» вышел, в «Сделано»
            # не попал. None у незакрытых и у закрытых до появления колонки.
            "completed_on": msk_date(state.completed_at)
            if state is not None and state.completed_at is not None
            else None,
        })
    return entries


def close_task_for_user(
    db: Session, task: TrackerTask, user_id: int, *, source: str
) -> TrackerTaskState:
    """Идемпотентно закрыть задачу по событию-источнику (видео досмотрено и т.п.).

    Только open → done, никогда done → open: система не должна откатывать
    задачу, которую ученик уже закрыл руками (или закрыла сама раньше —
    повторный heartbeat плеера не первое событие «стало done»).
    `completed_by_id` остаётся `None` — по докстроке `TrackerTaskState`
    это и значит «закрыла система», `completion_source` несёт, каким событием.
    """
    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task.id, TrackerTaskState.user_id == user_id)
        .one_or_none()
    )
    if state is None:
        state = TrackerTaskState(task_id=task.id, user_id=user_id, status=STATUS_OPEN)
        db.add(state)
    if state.status != STATUS_DONE:
        state.status = STATUS_DONE
        state.completed_at = now_msk()
        state.completed_by_id = None
        state.completion_source = source
    return state


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


# ---------------------------------------------------------------------------
# Неделя программы
# ---------------------------------------------------------------------------

def week_audience(db: Session, topic: LearningTopic) -> int:
    """Сколько учеников получат неделю целиком.

    Элемент недели своей адресации не имеет: аудиторию задаёт неделя, и охват
    считается её же функцией из видеомодуля. Копировать теги недели в каждый
    элемент нельзя — при смене аудитории недели копии молча разъехались бы.
    """
    return count_topic_audience(
        db,
        assign_to_all=topic.assign_to_all,
        tag_ids=topic_tag_ids(db, topic.id),
        assignee_ids=topic_assignee_ids(db, topic.id),
    )


def copy_week(
    db: Session, source: LearningTopic, *, user_id: int, shift_days: int = 7
) -> LearningTopic:
    """Скопировать неделю целиком со сдвигом дат — прошлая неделя как образец.

    Даты абсолютные (решение владельца от 20.08), поэтому копия просто сдвигает
    их на `shift_days` вперёд. Копия создаётся **черновиком**: неделя, уехавшая
    к ученикам в момент нажатия кнопки, — не то, чего ждут от копирования.

    Домашние задания дублируются, а не переиспользуются: иначе правка задания в
    новой неделе меняла бы задание в прошлой, уже сданной.
    """
    shift = timedelta(days=shift_days)
    copy = create_topic(
        db,
        title=f"{source.title} (копия)",
        description=source.description,
        opens_at=source.opens_at + shift,
        assign_to_all=source.assign_to_all,
        user_id=user_id,
    )
    copy.meeting_url = source.meeting_url
    set_topic_tags(db, copy, topic_tag_ids(db, source.id))
    set_topic_assignees(db, copy, topic_assignee_ids(db, source.id))

    for item in list_week_items(db, source.id):
        source_id = item.source_id
        if item.source_kind == SOURCE_HOMEWORK and item.source_id:
            duplicate = copy_homework(db, item.source_id, user_id=user_id)
            source_id = duplicate.id if duplicate else None
        elif item.source_kind == SOURCE_LEARNING_TOPIC:
            # Видео привязано к своей неделе, иначе копия вела бы на прошлую.
            source_id = copy.id
        create_task(
            db,
            title=item.title,
            description=item.description,
            due_at=item.due_at + shift if item.due_at else None,
            starts_at=item.starts_at + shift if item.starts_at else None,
            subject=item.subject,
            topic_id=copy.id,
            kind=item.kind,
            sort_order=item.sort_order,
            source_kind=item.source_kind,
            source_id=source_id,
            user_id=user_id,
        )
    db.flush()
    return copy


# ---------------------------------------------------------------------------
# Домашнее задание — отдельная сущность (решение владельца по Р2)
# ---------------------------------------------------------------------------

def get_homework(db: Session, homework_id: int) -> HomeworkAssignment | None:
    homework = db.get(HomeworkAssignment, homework_id)
    if homework is None or homework.deleted_at is not None:
        return None
    return homework


def create_homework(
    db: Session,
    *,
    title: str,
    user_id: int,
    description: str | None = None,
    subject: str | None = None,
    submission_required: bool = True,
    max_files: int = 1,
) -> HomeworkAssignment:
    homework = HomeworkAssignment(
        title=title,
        description=description,
        subject=subject,
        submission_required=submission_required,
        max_files=max_files,
        created_by_id=user_id,
    )
    db.add(homework)
    db.flush()
    return homework


def update_homework(
    homework: HomeworkAssignment,
    *,
    title: str,
    description: str | None = None,
    subject: str | None = None,
    submission_required: bool = True,
    max_files: int = 1,
) -> None:
    homework.title = title
    homework.description = description
    homework.subject = subject
    homework.submission_required = submission_required
    homework.max_files = max_files


def homework_images(db: Session, homework_id: int) -> list[HomeworkImage]:
    return (
        db.query(HomeworkImage)
        .filter(HomeworkImage.homework_id == homework_id)
        .order_by(HomeworkImage.sort_order.asc(), HomeworkImage.id.asc())
        .all()
    )


def set_homework_images(
    db: Session, homework: HomeworkAssignment, images: list[dict]
) -> None:
    """Переписать список картинок целиком. Каждая — `{"url": ..., "path": ...}`.

    Файлы из S3 не удаляем: та же картинка может быть у копии задания в другой
    неделе, а «прибраться в хранилище» ценой пустого квадрата в чужой неделе —
    плохая сделка.
    """
    db.query(HomeworkImage).filter(
        HomeworkImage.homework_id == homework.id
    ).delete(synchronize_session=False)
    for order, image in enumerate(images):
        url = (image.get("url") or "").strip()
        if not url:
            continue
        db.add(
            HomeworkImage(
                homework_id=homework.id,
                image_s3_url=url[:500],
                image_s3_path=(image.get("path") or None),
                sort_order=order,
            )
        )
    db.flush()


def copy_homework(
    db: Session, homework_id: int, *, user_id: int
) -> HomeworkAssignment | None:
    source = get_homework(db, homework_id)
    if source is None:
        return None
    duplicate = create_homework(
        db,
        title=source.title,
        description=source.description,
        subject=source.subject,
        submission_required=source.submission_required,
        max_files=source.max_files,
        user_id=user_id,
    )
    for image in homework_images(db, source.id):
        db.add(
            HomeworkImage(
                homework_id=duplicate.id,
                image_s3_url=image.image_s3_url,
                image_s3_path=image.image_s3_path,
                sort_order=image.sort_order,
            )
        )
    db.flush()
    return duplicate
