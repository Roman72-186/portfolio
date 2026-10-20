"""Нажатия «Подключиться» у событий дайджеста — кто открывал ссылку занятия.

Служба заботы 18.10.2026: «работает штука с отслеживанием, кто был на
занятии через кнопку?» — не работала, кнопка была голой ссылкой. Владелец
20.10.2026 решил записывать нажатия и показывать их в карточке ученика.

Нажатие значит «открыл ссылку», а не «просидел занятие»: сколько человек был
на созвоне, платформа не знает — созвон идёт в чужом сервисе.

Пишется в общий журнал `StudentActivityEvent` (тип `meeting_join`,
`details` — `event:<id>`), своей таблицы нет: журнал уже читают лента
карточки и сводка «Статистики активности». На одно событие ученику — одна
запись, первое нажатие: повторные тапы по той же кнопке лишь размножили бы
строки ленты. Название берётся из события при показе, поэтому правка
названия в дайджесте видна и в старых записях.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.activity_event import StudentActivityEvent
from app.models.tracker import ScheduleEvent
from app.services.tracker import (
    accessible_digest_ids,
    events_for_tariff,
    format_event_dates,
    format_event_time,
)

EVENT_MEETING_JOIN = "meeting_join"
_DETAILS_PREFIX = "event:"

# Событие из дайджеста удалили — запись о нажатии остаётся, имени уже нет.
DELETED_EVENT_TITLE = "событие удалено из дайджеста"

# Сколько подключений показывает карточка ученика.
JOINS_LIMIT = 50


def _details(event_id: int) -> str:
    return f"{_DETAILS_PREFIX}{event_id}"


def event_id_from_details(details: str | None) -> int | None:
    if not details or not details.startswith(_DETAILS_PREFIX):
        return None
    raw = details.removeprefix(_DETAILS_PREFIX)
    return int(raw) if raw.isdigit() else None


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


def record_meeting_join(db: Session, *, user_id: int, event: ScheduleEvent) -> bool:
    """Записать первое нажатие ученика по событию. True — запись новая."""
    exists = (
        db.query(StudentActivityEvent.id)
        .filter(
            StudentActivityEvent.user_id == user_id,
            StudentActivityEvent.event_type == EVENT_MEETING_JOIN,
            StudentActivityEvent.details == _details(event.id),
        )
        .first()
    )
    if exists is not None:
        return False
    db.add(StudentActivityEvent(
        user_id=user_id, event_type=EVENT_MEETING_JOIN, details=_details(event.id),
    ))
    db.flush()
    return True


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


def student_meeting_joins(db: Session, student_id: int) -> list[dict]:
    """Подключения ученика, свежие сверху: событие и когда нажал.

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
    titles = event_titles(db, (event_id_from_details(r.details) for r in rows))
    result: list[dict] = []
    for row in rows:
        event_id = event_id_from_details(row.details)
        joined_at: datetime = row.created_at
        result.append({
            "event": titles.get(event_id, DELETED_EVENT_TITLE),
            "joined_at": joined_at,
        })
    return result
