"""Задачи «Личного трекера»: адресация, охват и управление.

Source of truth по тому, кому адресована задача и кто её увидит. Адресация
скопирована с тем видеоуроков (`video_topics.py`): явный флаг «всем», теги,
поимённые исключения. **Сопоставление тегов строгое, по `tag_id`** — эвристика
предметов из пробников («Р» и «К» как маркеры предмета) в проде означает группу
и уровень куратора и уже прятала билеты от учеников (`TODO.md` §7).

Разбор решений владельца — plans/2026-08-20-apparchi-tracker-and-digest.md.
"""

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app.constants import MOCK_SUBJECTS
from app.models.exam_cycle import ExamCycle
from app.models.homework import HomeworkAssignment, HomeworkImage
from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.role import Role
from app.models.tag import UserTag
from app.models.tracker import (
    ITEM_HOMEWORK,
    ITEM_KIND_LABELS,
    ITEM_MOCK_EXAM,
    ITEM_OTHER,
    SOURCE_HOMEWORK,
    SOURCE_LEARNING_TOPIC,
    STATUS_DONE,
    STATUS_OPEN,
    TAB_KIND_FEEDBACK,
    WEEK_TAB_LABELS,
    WEEK_TAB_SEQUENCE,
    ScheduleDigest,
    ScheduleDigestAssignee,
    ScheduleDigestTag,
    ScheduleEvent,
    TrackerGoal,
    TrackerGoalAssignee,
    TrackerGoalTag,
    TrackerTask,
    TrackerTaskAssignee,
    TrackerTaskState,
    TrackerTaskTag,
)
from app.models.user import User
from app.services.program import day_bounds, msk_date, week_start
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
    is_required: bool = True,
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
        is_required=is_required,
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
    is_required: bool = True,
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
    task.is_required = is_required


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


def build_week_tabs(entries: list[dict]) -> list[dict]:
    """Восемь вкладок недели АОП в фиксированном порядке `WEEK_TAB_SEQUENCE`,
    с блокировкой «следующая вкладка открыта, только когда закрыта предыдущая»
    (решение владельца 22.08/23.08).

    Чистая функция без похода в БД: `entries` уже посчитаны
    `accessible_task_entries` вместе со статусом. Вкладка без единой задачи
    (преподаватель просто ничего не поставил в неё на эту неделю) блокировать
    нечем — она пропускает цепочку дальше, не запирая следующую. Причина
    блокировки («Сначала сделай …») держится на первой реально незакрытой
    вкладке и не сдвигается, пока по ней не появится статус done у всех задач.

    Билет Пробника (`ITEM_MOCK_EXAM`) — отдельный от домашки механизм
    (`ExamCycle`/`ExamTicket`, не сливается), но решением владельца 24.08
    показывается ученику внутри вкладки «Задание», а не своей карточкой вне
    вкладок (было решением 22.08). У `ITEM_MOCK_EXAM` своей позиции в
    `WEEK_TAB_SEQUENCE` нет и не появляется — его записи просто досыпаются в
    бакет `ITEM_HOMEWORK` перед раскладкой. Это только про отображение:
    незакрытый билет не запирает следующие вкладки этой же недели и не
    участвует в недельном гейте (см. `is_week_complete`, которая его тоже
    исключает) — по решению владельца 24.08 Пробник продолжает блокировать
    только переход на следующий месяц, к неделе отношения не имеет.
    """
    by_kind: dict[str, list[dict]] = {}
    for entry in entries:
        by_kind.setdefault(entry["task"].kind, []).append(entry)

    mock_entries = by_kind.pop(ITEM_MOCK_EXAM, [])
    if mock_entries:
        by_kind[ITEM_HOMEWORK] = by_kind.get(ITEM_HOMEWORK, []) + mock_entries

    tabs = []
    locked_reason: str | None = None
    for kind in WEEK_TAB_SEQUENCE:
        tab_entries = by_kind.get(kind, [])
        is_locked = locked_reason is not None
        tabs.append({
            "kind": kind,
            "label": WEEK_TAB_LABELS[kind],
            "entries": tab_entries,
            "is_locked": is_locked,
            "locked_reason": locked_reason,
            "reserved": kind == TAB_KIND_FEEDBACK,
        })
        # Билет Пробника участвует в отображении вкладки «Задание», но не в
        # проверке «закрыта ли вкладка» — иначе он запирал бы «Чек-лист» и
        # всё, что после, хотя решение владельца отводит ему только месячный
        # уровень блокировки.
        lock_entries = [e for e in tab_entries if e["task"].kind != ITEM_MOCK_EXAM]
        if not is_locked and lock_entries and any(e["status"] != "done" for e in lock_entries):
            locked_reason = WEEK_TAB_LABELS[kind]
    return tabs


# ---------------------------------------------------------------------------
# Гейт «блок → неделя → месяц» (решение владельца 23.08)
# ---------------------------------------------------------------------------

def is_week_complete(db: Session, user_id: int, week_monday: date) -> bool:
    """Неделя пройдена: все обязательные опубликованные задачи недели,
    доступные этому ученику, закрыты. Без разреза по предмету — оба предмета
    в одной проверке (решение владельца 23.08).

    Выборка задач — то же окно `[Monday, Monday+7)` по `due_at`, что видит
    экран «Актуальное образовательное пространство» (`accessible_task_entries`):
    у каждого элемента программы своя одноразовая служебная тема
    (`program.py::ensure_item_topic` заводит новую при каждом добавлении в
    день), общего `LearningTopic`, объединяющего все элементы недели по
    `topic_id`, в схеме нет.

    Билет Пробника (`ITEM_MOCK_EXAM`) в эту проверку не входит намеренно: он
    отображается внутри вкладки «Задание» (решение владельца 24.08), но
    блокирует только переход на следующий месяц (`_mock_exams_closed_for_month`
    ниже), к неделе отношения не имеет — так было решено ещё 23.08 и
    подтверждено 24.08.
    """
    start, _ = day_bounds(week_monday)
    _, end = day_bounds(week_monday + timedelta(days=6))
    entries = accessible_task_entries(db, user_id, start=start, end=end)
    return all(
        entry["status"] == "done"
        for entry in entries
        if entry["task"].is_required and entry["task"].kind != ITEM_MOCK_EXAM
    )


def _enrollment_monday(created_at: datetime) -> date:
    """Ближайший понедельник после регистрации — если регистрация ровно в
    понедельник, это тот же день (решение владельца 23.08, п.3)."""
    created_date = msk_date(created_at)
    monday = week_start(created_date)
    return monday if monday == created_date else monday + timedelta(days=7)


def _accessible_week_mondays(
    db: Session, user_id: int, *, start: date, end: date
) -> list[date]:
    """Понедельники доступных ученику недель (`LearningTopic(kind='week')`),
    попадающих в `[start, end]`, по возрастанию, без дублей."""
    topic_ids = accessible_topic_ids(db, user_id)
    if not topic_ids:
        return []
    topics = (
        db.query(LearningTopic)
        .filter(LearningTopic.id.in_(topic_ids), LearningTopic.kind == TOPIC_KIND_WEEK)
        .all()
    )
    mondays = {week_start(msk_date(topic.opens_at)) for topic in topics}
    return sorted(m for m in mondays if start <= m <= end)


def effective_week_start(db: Session, user_id: int, today: date) -> date:
    """Первая незакрытая неделя ученика, ограниченная снизу его понедельником
    (опоздавший не утаскивается на неделю месячной давности) и сверху
    сегодняшней календарной неделей (без довыдачи вперёд). Если долгов нет —
    возвращает текущую календарную неделю."""
    user = db.get(User, user_id)
    enrollment_monday = _enrollment_monday(user.created_at)
    today_monday = week_start(today)
    for monday in _accessible_week_mondays(db, user_id, start=enrollment_monday, end=today_monday):
        if not is_week_complete(db, user_id, monday):
            return monday
    return today_monday


def _mock_exams_closed_for_month(
    db: Session, user_id: int, month_start: date, month_end: date
) -> bool:
    """Пробник закрыт по обоим предметам в границах месяца. Отсутствие цикла
    по предмету в этом месяце тоже блокирует месяц — ждём (решение владельца
    23.08, п.4)."""
    for subject in MOCK_SUBJECTS:
        cycle = (
            db.query(ExamCycle)
            .filter(
                ExamCycle.user_id == user_id,
                ExamCycle.subject == subject,
                ExamCycle.started_at >= month_start,
                ExamCycle.started_at <= month_end,
            )
            .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
            .first()
        )
        if cycle is None or cycle.closed_at is None:
            return False
    return True


def week_topic_for_monday(db: Session, user_id: int, monday: date) -> LearningTopic | None:
    """Тема недели (`kind=week`), чей понедельник — `monday`, доступная ученику.

    Резолвер для гейта: в отличие от `video_topics.py::current_week_topic`
    («самая поздняя из открытых»), берёт ровно ту неделю, на которой стоит
    ученик по `effective_week_start` — включая прошлую, если он на ней
    застрял. `None`, если темы для этой недели нет вовсе — тот же случай, что
    и раньше у `current_week_topic`.
    """
    topic_ids = accessible_topic_ids(db, user_id)
    if not topic_ids:
        return None
    candidates = (
        db.query(LearningTopic)
        .filter(LearningTopic.id.in_(topic_ids), LearningTopic.kind == TOPIC_KIND_WEEK)
        .all()
    )
    return next(
        (topic for topic in candidates if week_start(msk_date(topic.opens_at)) == monday),
        None,
    )


def is_month_complete(db: Session, user_id: int, year: int, month: int) -> bool:
    """Месяц пройден: все недели месяца закрыты (то же правило недели,
    применённое к каждой) плюс отдельно закрыт Пробник по обоим предметам —
    он вне восьми вкладок недели (решение владельца 22.08), в понедельниках
    недель не участвует."""
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    weeks_done = all(
        is_week_complete(db, user_id, monday)
        for monday in _accessible_week_mondays(db, user_id, start=month_start, end=month_end)
    )
    if not weeks_done:
        return False
    return _mock_exams_closed_for_month(db, user_id, month_start, month_end)


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


# ---------------------------------------------------------------------------
# Дайджест-расписание месяца (фаза 5, plans/2026-08-20-apparchi-tracker-and-digest.md)
# ---------------------------------------------------------------------------

def list_digests(db: Session, *, include_deleted: bool = False) -> list[ScheduleDigest]:
    """Дайджесты для списка преподавателя: свежий месяц сверху."""
    query = db.query(ScheduleDigest)
    if not include_deleted:
        query = query.filter(ScheduleDigest.deleted_at.is_(None))
    return query.order_by(
        ScheduleDigest.year.desc(), ScheduleDigest.month.desc(), ScheduleDigest.id.desc()
    ).all()


def get_digest(db: Session, digest_id: int) -> ScheduleDigest | None:
    digest = db.get(ScheduleDigest, digest_id)
    if digest is None or digest.deleted_at is not None:
        return None
    return digest


def create_digest(
    db: Session, *, title: str, year: int, month: int, assign_to_all: bool, user_id: int
) -> ScheduleDigest:
    digest = ScheduleDigest(
        title=title,
        year=year,
        month=month,
        assign_to_all=assign_to_all,
        created_by_id=user_id,
    )
    db.add(digest)
    db.flush()
    return digest


def update_digest(
    digest: ScheduleDigest, *, title: str, year: int, month: int, assign_to_all: bool
) -> None:
    digest.title = title
    digest.year = year
    digest.month = month
    digest.assign_to_all = assign_to_all


def set_digest_tags(db: Session, digest: ScheduleDigest, tag_ids: list[int]) -> None:
    db.query(ScheduleDigestTag).filter(
        ScheduleDigestTag.digest_id == digest.id
    ).delete(synchronize_session=False)
    for tag_id in dict.fromkeys(tag_ids):
        db.add(ScheduleDigestTag(digest_id=digest.id, tag_id=tag_id))
    db.flush()


def set_digest_assignees(db: Session, digest: ScheduleDigest, user_ids: list[int]) -> None:
    db.query(ScheduleDigestAssignee).filter(
        ScheduleDigestAssignee.digest_id == digest.id
    ).delete(synchronize_session=False)
    for user_id in dict.fromkeys(user_ids):
        db.add(ScheduleDigestAssignee(digest_id=digest.id, user_id=user_id))
    db.flush()


def get_digest_tag_ids(db: Session, digest_id: int) -> list[int]:
    rows = (
        db.query(ScheduleDigestTag.tag_id)
        .filter(ScheduleDigestTag.digest_id == digest_id)
        .all()
    )
    return [row[0] for row in rows]


def get_digest_assignee_ids(db: Session, digest_id: int) -> list[int]:
    rows = (
        db.query(ScheduleDigestAssignee.user_id)
        .filter(ScheduleDigestAssignee.digest_id == digest_id)
        .all()
    )
    return [row[0] for row in rows]


def count_digest_audience(
    db: Session, *, assign_to_all: bool, tag_ids: list[int], assignee_ids: list[int]
) -> int:
    """Сколько активных учеников увидят этот дайджест — тот же расчёт, что у
    задач (`count_task_audience`), без фильтра по членству в группе по той же
    причине: `/cabinet/tracker` закрыт `require_student`, а не
    `require_learning_content_access`."""
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


def publish_digest(digest: ScheduleDigest, *, user_id: int) -> None:
    if digest.deleted_at is not None:
        raise ValueError("Digest is deleted")
    digest.is_published = True
    digest.published_at = datetime.now(timezone.utc)
    digest.published_by_id = user_id


def unpublish_digest(digest: ScheduleDigest) -> None:
    digest.is_published = False
    digest.published_at = None
    digest.published_by_id = None


def delete_digest(digest: ScheduleDigest) -> None:
    """Мягкое удаление, только со снятой публикации — тот же довод, что у
    задач: опубликованный дайджест уже висит у учеников."""
    digest.deleted_at = datetime.now(timezone.utc)
    digest.is_published = False


def list_events(db: Session, digest_id: int) -> list[ScheduleEvent]:
    return (
        db.query(ScheduleEvent)
        .filter(ScheduleEvent.digest_id == digest_id)
        .order_by(ScheduleEvent.starts_on.asc(), ScheduleEvent.sort_order.asc(), ScheduleEvent.id.asc())
        .all()
    )


def get_event(db: Session, event_id: int) -> ScheduleEvent | None:
    return db.get(ScheduleEvent, event_id)


def create_event(
    db: Session,
    digest_id: int,
    *,
    kind: str,
    title: str,
    note: str | None,
    starts_on: date,
    ends_on: date,
    meeting_url: str | None,
    sort_order: int = 0,
) -> ScheduleEvent:
    event = ScheduleEvent(
        digest_id=digest_id,
        kind=kind,
        title=title,
        note=note,
        starts_on=starts_on,
        ends_on=ends_on,
        meeting_url=meeting_url,
        sort_order=sort_order,
    )
    db.add(event)
    db.flush()
    return event


def update_event(
    event: ScheduleEvent,
    *,
    kind: str,
    title: str,
    note: str | None,
    starts_on: date,
    ends_on: date,
    meeting_url: str | None,
    sort_order: int = 0,
) -> None:
    event.kind = kind
    event.title = title
    event.note = note
    event.starts_on = starts_on
    event.ends_on = ends_on
    event.meeting_url = meeting_url
    event.sort_order = sort_order


def delete_event(db: Session, event: ScheduleEvent) -> None:
    """Жёсткое удаление: в отличие от задачи, на событие расписания ничего не
    ссылается — у него нет ни состояния ученика, ни истории выполнения."""
    db.delete(event)
    db.flush()


def accessible_digest_ids(db: Session, user_id: int) -> set[int]:
    """Опубликованные дайджесты, адресованные ученику — зеркало
    `accessible_task_ids`, но по ScheduleDigestTag/ScheduleDigestAssignee."""
    user_tag_ids = (
        db.query(UserTag.tag_id).filter(UserTag.user_id == user_id).scalar_subquery()
    )
    tagged_ids = (
        db.query(ScheduleDigestTag.digest_id)
        .filter(ScheduleDigestTag.tag_id.in_(user_tag_ids))
        .scalar_subquery()
    )
    assigned_ids = (
        db.query(ScheduleDigestAssignee.digest_id)
        .filter(ScheduleDigestAssignee.user_id == user_id)
        .scalar_subquery()
    )
    rows = (
        db.query(ScheduleDigest.id)
        .filter(
            ScheduleDigest.deleted_at.is_(None),
            ScheduleDigest.is_published.is_(True),
            or_(
                ScheduleDigest.assign_to_all.is_(True),
                ScheduleDigest.id.in_(tagged_ids),
                ScheduleDigest.id.in_(assigned_ids),
            ),
        )
        .all()
    )
    return {row[0] for row in rows}


def active_digest_for_student(
    db: Session, user_id: int, *, year: int, month: int
) -> ScheduleDigest | None:
    """Дайджест месяца, который видит этот ученик.

    Может подходить несколько (адресация по группам/тарифам, решение
    владельца 20.08) — берём опубликованный последним: он точнее отражает
    финальную версию расписания на случай, если преподаватель пересобирал
    дайджест несколько раз.
    """
    digest_ids = accessible_digest_ids(db, user_id)
    if not digest_ids:
        return None
    return (
        db.query(ScheduleDigest)
        .filter(
            ScheduleDigest.id.in_(digest_ids),
            ScheduleDigest.year == year,
            ScheduleDigest.month == month,
        )
        .order_by(ScheduleDigest.published_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# «Ближайшая цель» — ручная карточка преподавателя (решение владельца 23.08)
# ---------------------------------------------------------------------------

def list_goals(db: Session, *, include_deleted: bool = False) -> list[TrackerGoal]:
    """Цели для списка преподавателя: без даты начала — внизу, иначе по дате."""
    query = db.query(TrackerGoal)
    if not include_deleted:
        query = query.filter(TrackerGoal.deleted_at.is_(None))
    no_start_last = case((TrackerGoal.starts_on.is_(None), 1), else_=0)
    return query.order_by(
        no_start_last, TrackerGoal.starts_on.asc(), TrackerGoal.id.desc()
    ).all()


def get_goal(db: Session, goal_id: int) -> TrackerGoal | None:
    goal = db.get(TrackerGoal, goal_id)
    if goal is None or goal.deleted_at is not None:
        return None
    return goal


def create_goal(
    db: Session,
    *,
    title: str,
    user_id: int,
    description: str | None = None,
    target_score: int | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
    assign_to_all: bool = False,
) -> TrackerGoal:
    goal = TrackerGoal(
        title=title,
        description=description,
        target_score=target_score,
        starts_on=starts_on,
        ends_on=ends_on,
        assign_to_all=assign_to_all,
        created_by_id=user_id,
    )
    db.add(goal)
    db.flush()
    return goal


def update_goal(
    goal: TrackerGoal,
    *,
    title: str,
    description: str | None = None,
    target_score: int | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
    assign_to_all: bool = False,
) -> None:
    goal.title = title
    goal.description = description
    goal.target_score = target_score
    goal.starts_on = starts_on
    goal.ends_on = ends_on
    goal.assign_to_all = assign_to_all


def set_goal_tags(db: Session, goal: TrackerGoal, tag_ids: list[int]) -> None:
    db.query(TrackerGoalTag).filter(TrackerGoalTag.goal_id == goal.id).delete(
        synchronize_session=False
    )
    for tag_id in dict.fromkeys(tag_ids):
        db.add(TrackerGoalTag(goal_id=goal.id, tag_id=tag_id))
    db.flush()


def set_goal_assignees(db: Session, goal: TrackerGoal, user_ids: list[int]) -> None:
    db.query(TrackerGoalAssignee).filter(
        TrackerGoalAssignee.goal_id == goal.id
    ).delete(synchronize_session=False)
    for user_id in dict.fromkeys(user_ids):
        db.add(TrackerGoalAssignee(goal_id=goal.id, user_id=user_id))
    db.flush()


def get_goal_tag_ids(db: Session, goal_id: int) -> list[int]:
    rows = db.query(TrackerGoalTag.tag_id).filter(TrackerGoalTag.goal_id == goal_id).all()
    return [row[0] for row in rows]


def get_goal_assignee_ids(db: Session, goal_id: int) -> list[int]:
    rows = (
        db.query(TrackerGoalAssignee.user_id)
        .filter(TrackerGoalAssignee.goal_id == goal_id)
        .all()
    )
    return [row[0] for row in rows]


def count_goal_audience(
    db: Session, *, assign_to_all: bool, tag_ids: list[int], assignee_ids: list[int]
) -> int:
    """Тот же расчёт охвата, что у задач и дайджеста — без фильтра по
    членству в группе, по той же причине (`/cabinet/tracker` закрыт
    `require_student`, не `require_learning_content_access`)."""
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


def publish_goal(goal: TrackerGoal, *, user_id: int) -> None:
    if goal.deleted_at is not None:
        raise ValueError("Goal is deleted")
    goal.is_published = True
    goal.published_at = datetime.now(timezone.utc)
    goal.published_by_id = user_id


def unpublish_goal(goal: TrackerGoal) -> None:
    goal.is_published = False
    goal.published_at = None
    goal.published_by_id = None


def delete_goal(goal: TrackerGoal) -> None:
    """Мягкое удаление, только со снятой публикации — тот же довод, что у
    задач и дайджеста."""
    goal.deleted_at = datetime.now(timezone.utc)
    goal.is_published = False


def accessible_goal_ids(db: Session, user_id: int) -> set[int]:
    """Опубликованные цели, адресованные ученику — зеркало
    `accessible_digest_ids`, но по TrackerGoalTag/TrackerGoalAssignee."""
    user_tag_ids = (
        db.query(UserTag.tag_id).filter(UserTag.user_id == user_id).scalar_subquery()
    )
    tagged_ids = (
        db.query(TrackerGoalTag.goal_id)
        .filter(TrackerGoalTag.tag_id.in_(user_tag_ids))
        .scalar_subquery()
    )
    assigned_ids = (
        db.query(TrackerGoalAssignee.goal_id)
        .filter(TrackerGoalAssignee.user_id == user_id)
        .scalar_subquery()
    )
    rows = (
        db.query(TrackerGoal.id)
        .filter(
            TrackerGoal.deleted_at.is_(None),
            TrackerGoal.is_published.is_(True),
            or_(
                TrackerGoal.assign_to_all.is_(True),
                TrackerGoal.id.in_(tagged_ids),
                TrackerGoal.id.in_(assigned_ids),
            ),
        )
        .all()
    )
    return {row[0] for row in rows}


def active_goal_for_student(db: Session, user_id: int, *, today: date) -> TrackerGoal | None:
    """Ближайшая ещё не завершившаяся цель, адресованная ученику.

    «Ближайшая» — по дате начала, раньше вперёд; цели без даты начала идут
    после датированных (их «близость» не определена, но прятать их совсем
    было бы неверно). Уже прошедшие (`ends_on` в прошлом) не показываются.
    """
    goal_ids = accessible_goal_ids(db, user_id)
    if not goal_ids:
        return None
    no_start_last = case((TrackerGoal.starts_on.is_(None), 1), else_=0)
    return (
        db.query(TrackerGoal)
        .filter(
            TrackerGoal.id.in_(goal_ids),
            or_(TrackerGoal.ends_on.is_(None), TrackerGoal.ends_on >= today),
        )
        .order_by(no_start_last, TrackerGoal.starts_on.asc(), TrackerGoal.id.desc())
        .first()
    )
