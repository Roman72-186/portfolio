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
from datetime import datetime

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
    """Переходы ученика по ссылкам занятий, свежие сверху: что и когда.

    `joined_at` — время в UTC, переводит в Москву вызывающий, как остальные
    поля карточки (`activity_stats._when`)."""
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
    titles = join_titles(db, (r.details for r in rows))
    result: list[dict] = []
    for row in rows:
        joined_at: datetime = row.created_at
        result.append({
            "event": titles.get(row.details, DELETED_EVENT_TITLE),
            "joined_at": joined_at,
        })
    return result
