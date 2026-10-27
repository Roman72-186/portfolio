"""Переходы по ссылкам занятий — кто открывал ссылку и когда.

Служба заботы 18.10.2026: «работает штука с отслеживанием, кто был на
занятии через кнопку?» — не работала, кнопка была голой ссылкой. Владелец
20.10.2026 решил записывать нажатия и показывать их в карточке ученика.

Источников два, журнал один:
- «Подключиться» у события дайджеста (`details` — `event:<id>`, с 20.10.2026);
- кнопка блока «Ссылка» в задании (`details` — `block:<id>`, с 27.10.2026).
  Школа ставит Zoom именно сюда: на 27.10.2026 ни у одного события дайджеста
  ссылки не было, и раздел «Занятия» в карточке неделю стоял пустым у всех
  (Александрия 27.10.2026: «я не нашла»). Блок «Ссылка» бывает и не про
  занятие (документ «Статистика»), поэтому строка подписана названием блока
  и задания — что открыто, видно по подписи.

Нажатие значит «открыл ссылку», а не «просидел занятие»: сколько человек был
на созвоне, платформа не знает — созвон идёт в чужом сервисе.

Пишется в общий журнал `StudentActivityEvent` (тип `meeting_join`), своей
таблицы нет: журнал уже читают лента карточки и сводка «Статистики
активности». На одну ссылку ученику — одна запись, первое нажатие:
повторные тапы по той же кнопке лишь размножили бы строки ленты. Название
берётся при показе, поэтому правка названия видна и в старых записях.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.activity_event import StudentActivityEvent
from app.models.task_block import TaskBlock
from app.models.tracker import ScheduleEvent, TrackerTask
from app.services.tracker import (
    accessible_digest_ids,
    events_for_tariff,
    format_event_dates,
    format_event_time,
)

EVENT_MEETING_JOIN = "meeting_join"
_DETAILS_PREFIX = "event:"
_BLOCK_PREFIX = "block:"

# Событие или блок удалили — запись о нажатии остаётся, имени уже нет.
DELETED_EVENT_TITLE = "событие удалено из дайджеста"
DELETED_BLOCK_TITLE = "ссылка удалена из задания"
# Блок «Ссылка» без названия ученик видит кнопкой с этой подписью.
UNTITLED_BLOCK_TITLE = "Ссылка"

# Сколько подключений показывает карточка ученика.
JOINS_LIMIT = 50


def _details(event_id: int) -> str:
    return f"{_DETAILS_PREFIX}{event_id}"


def _id_from_details(details: str | None, prefix: str) -> int | None:
    if not details or not details.startswith(prefix):
        return None
    raw = details.removeprefix(prefix)
    return int(raw) if raw.isdigit() else None


def event_id_from_details(details: str | None) -> int | None:
    return _id_from_details(details, _DETAILS_PREFIX)


def block_id_from_details(details: str | None) -> int | None:
    return _id_from_details(details, _BLOCK_PREFIX)


def visible_meeting_event(
    db: Session, *, user_id: int, tariff: str | None, event_id: int
) -> ScheduleEvent | None:
    """Событие со ссылкой, которое ученику показано: дайджест опубликован и
    адресован ему, тариф подходит. То же правило, что рисует ему кнопку
    (`accessible_digest_ids` + `events_for_tariff`), — иначе в статистику
    попадали бы «подключения» к чужим занятиям."""
    event = db.get(ScheduleEvent, event_id)
    if event is None or not event.meeting_url:
        return None
    if event.digest_id not in accessible_digest_ids(db, user_id):
        return None
    if not events_for_tariff(db, [event], tariff):
        return None
    return event


def _record_first(db: Session, *, user_id: int, details: str) -> bool:
    exists = (
        db.query(StudentActivityEvent.id)
        .filter(
            StudentActivityEvent.user_id == user_id,
            StudentActivityEvent.event_type == EVENT_MEETING_JOIN,
            StudentActivityEvent.details == details,
        )
        .first()
    )
    if exists is not None:
        return False
    db.add(StudentActivityEvent(
        user_id=user_id, event_type=EVENT_MEETING_JOIN, details=details,
    ))
    db.flush()
    return True


def record_meeting_join(db: Session, *, user_id: int, event: ScheduleEvent) -> bool:
    """Записать первое нажатие ученика по событию. True — запись новая."""
    return _record_first(db, user_id=user_id, details=_details(event.id))


def record_link_open(db: Session, *, user_id: int, block: TaskBlock) -> bool:
    """Записать первый переход ученика по блоку «Ссылка». True — запись новая.

    Доступ проверяет вызывающий (`go_link_block`): сюда приходит только
    переход, который сервер и так пропускает дальше."""
    return _record_first(db, user_id=user_id, details=f"{_BLOCK_PREFIX}{block.id}")


def link_opens(db: Session, block_ids) -> dict[tuple[int, int], datetime]:
    """Первые переходы по блокам «Ссылка»: (блок, ученик) → когда, в UTC.

    Режим «Занятия» в «Учениках поимённо» (`staff_dashboard._assignment_activity`):
    кто из адресатов ссылки нажимал её, а кто нет."""
    details = {f"{_BLOCK_PREFIX}{block_id}" for block_id in block_ids}
    if not details:
        return {}
    result: dict[tuple[int, int], datetime] = {}
    for user_id, raw, created_at in (
        db.query(
            StudentActivityEvent.user_id,
            StudentActivityEvent.details,
            StudentActivityEvent.created_at,
        )
        .filter(
            StudentActivityEvent.event_type == EVENT_MEETING_JOIN,
            StudentActivityEvent.details.in_(details),
        )
    ):
        result[(block_id_from_details(raw), user_id)] = created_at
    return result


# Откуда «был / не был» по кнопке «Ссылка» — для подписей в статистике.
ATTENDED_PRESSED = "pressed"
ATTENDED_STAFF = "staff_yes"
ABSENT_STAFF = "staff_no"

AUDIT_LESSON_ATTENDANCE = "lesson_attendance"


def attendance_by_block(db: Session, block_ids) -> dict[tuple[int, int], str]:
    """Кто был на занятии по каждой кнопке «Ссылка»: (блок, ученик) → откуда.

    Александрия 27.10.2026: нажал «Подключиться к занятию» — был на занятии.
    Отметка преподавателя (`LessonAttendanceMark`, владелец 27.10.2026)
    перекрывает нажатие в обе стороны, поэтому «не был» тоже бывает в ответе
    (`ABSENT_STAFF`). Нет ни нажатия, ни отметки — пары в ответе нет.
    **Единственное место правила**: лента (`attended_link_ids`), режим
    «Занятия» и карточка ученика читают его отсюда."""
    from app.models.task_block import LessonAttendanceMark

    ids = {block_id for block_id in block_ids if block_id is not None}
    if not ids:
        return {}
    result = {key: ATTENDED_PRESSED for key in link_opens(db, ids)}
    for mark in db.query(LessonAttendanceMark).filter(LessonAttendanceMark.block_id.in_(ids)):
        result[(mark.block_id, mark.user_id)] = ATTENDED_STAFF if mark.attended else ABSENT_STAFF
    return result


def attended_link_ids(db: Session, user_id: int, block_ids) -> set[int]:
    """Кнопки «Ссылка», по которым ученик был на занятии, — для обязательности
    записи (`task_blocks.required_by_block_for_task`). Одно чтение на набор."""
    from app.models.task_block import LessonAttendanceMark

    ids = {block_id for block_id in block_ids if block_id is not None}
    if not ids:
        return set()
    pressed = (
        db.query(StudentActivityEvent.details)
        .filter(
            StudentActivityEvent.user_id == user_id,
            StudentActivityEvent.event_type == EVENT_MEETING_JOIN,
            StudentActivityEvent.details.in_({f"{_BLOCK_PREFIX}{i}" for i in ids}),
        )
        .all()
    )
    attended = {block_id_from_details(details) for (details,) in pressed}
    for mark in db.query(LessonAttendanceMark).filter(
        LessonAttendanceMark.user_id == user_id,
        LessonAttendanceMark.block_id.in_(ids),
    ):
        if mark.attended:
            attended.add(mark.block_id)
        else:
            attended.discard(mark.block_id)
    return attended


def set_attendance(
    db: Session, *, block: TaskBlock, student_id: int, attended: bool, staff_id: int,
) -> None:
    """Отметка преподавателя «был / не был на занятии» (владелец 27.10.2026).

    Пишет журнал аудита и пересчитывает задание: «был» снимает обязательность
    записи, и если она одна оставалась — задание закрывается сразу, а не при
    следующем шаге ученика. Доступ проверяет вызывающий роут."""
    from app.models.audit_log import AuditLog
    from app.models.task_block import LessonAttendanceMark
    from app.services.task_blocks import maybe_close_task_by_blocks

    mark = db.get(LessonAttendanceMark, (block.id, student_id))
    if mark is None:
        mark = LessonAttendanceMark(block_id=block.id, user_id=student_id)
        db.add(mark)
    mark.attended = attended
    mark.marked_by_id = staff_id
    mark.marked_at = datetime.now(timezone.utc)
    title = _block_titles(db, [block.id]).get(block.id, UNTITLED_BLOCK_TITLE)
    db.add(AuditLog(
        action=AUDIT_LESSON_ATTENDANCE,
        performed_by_id=staff_id,
        target_user_id=student_id,
        details=f"{'Был' if attended else 'Не был'} на занятии: {title}"[:1000],
    ))
    db.flush()
    maybe_close_task_by_blocks(db, block.task_id, student_id)


def event_titles(db: Session, event_ids) -> dict[int, str]:
    """«Занятие по рисунку · 21 октября, 10:00–11:30» по номеру события."""
    ids = {i for i in event_ids if i is not None}
    if not ids:
        return {}
    titles = {}
    for event in db.query(ScheduleEvent).filter(ScheduleEvent.id.in_(ids)).all():
        when = format_event_dates(event)
        time_text = format_event_time(event)
        titles[event.id] = f"{event.title} · {when}" + (f", {time_text}" if time_text else "")
    return titles


def _block_titles(db: Session, block_ids) -> dict[int, str]:
    """«Подключение к занятию · Занятие 18.10» по номеру блока «Ссылка»."""
    ids = {i for i in block_ids if i is not None}
    if not ids:
        return {}
    rows = (
        db.query(TaskBlock.id, TaskBlock.title, TrackerTask.title)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .filter(TaskBlock.id.in_(ids))
        .all()
    )
    return {
        block_id: f"{(title or '').strip() or UNTITLED_BLOCK_TITLE} · {task_title}"
        for block_id, title, task_title in rows
    }


def join_titles(db: Session, details) -> dict[str, str]:
    """Подпись записи о нажатии по её `details` — для обоих источников.

    Нераспознанный `details` в ответ не попадает: что показать вместо него,
    решает вызывающий."""
    details = [d for d in details if d]
    events = event_titles(db, (event_id_from_details(d) for d in details))
    blocks = _block_titles(db, (block_id_from_details(d) for d in details))
    result: dict[str, str] = {}
    for d in details:
        event_id = event_id_from_details(d)
        if event_id is not None:
            result[d] = events.get(event_id, DELETED_EVENT_TITLE)
            continue
        block_id = block_id_from_details(d)
        if block_id is not None:
            result[d] = blocks.get(block_id, DELETED_BLOCK_TITLE)
    return result


def student_meeting_joins(db: Session, student_id: int) -> list[dict]:
    """Занятия, на которых ученик был, свежие сверху: подпись и откуда «был».

    «Был» — по тому же правилу, что лента и «Ученики поимённо»: нажатие или
    отметка преподавателя, и «не был» преподавателя убирает нажатие
    (владелец 27.10.2026). `joined_at` — время в UTC; карточка его не
    показывает («время нажатия вообще нужно убрать»)."""
    from app.models.task_block import LessonAttendanceMark

    marks = {
        mark.block_id: mark
        for mark in db.query(LessonAttendanceMark).filter(LessonAttendanceMark.user_id == student_id)
    }
    rows = (
        db.query(StudentActivityEvent)
        .filter(
            StudentActivityEvent.user_id == student_id,
            StudentActivityEvent.event_type == EVENT_MEETING_JOIN,
        )
        .order_by(StudentActivityEvent.created_at.desc())
        .limit(JOINS_LIMIT)
        .all()
    )
    entries: list[tuple[str, datetime, str]] = []
    for row in rows:
        mark = marks.get(block_id_from_details(row.details))
        if mark is not None and not mark.attended:
            continue
        entries.append((row.details, row.created_at, ATTENDED_PRESSED))
    pressed = {details for details, _, _ in entries}
    for block_id, mark in marks.items():
        details = f"{_BLOCK_PREFIX}{block_id}"
        if mark.attended and details not in pressed:
            entries.append((details, mark.marked_at, ATTENDED_STAFF))
    # Нажатие и отметка — из разных таблиц: сравниваем в UTC, наивное — как UTC.
    entries.sort(
        key=lambda entry: entry[1] if entry[1].tzinfo else entry[1].replace(tzinfo=timezone.utc),
        reverse=True,
    )
    titles = join_titles(db, (details for details, _, _ in entries))
    return [
        {"event": titles.get(details, DELETED_EVENT_TITLE), "joined_at": at, "source": source}
        for details, at, source in entries[:JOINS_LIMIT]
    ]
