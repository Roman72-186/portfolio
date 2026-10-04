"""Типы событий дайджеста: название, цвет метки, заливка или контур.

Владелец 04.10.2026: тип и цвет — одна связка, настраивает их Главный
преподаватель. Экран — `/cabinet/staff/digest/types` (`cabinet_digest_admin.py`).

Тип, на который ссылается хоть одно событие, не удаляется, а скрывается:
старые события рисуются как были, в форму нового события тип не попадает.
"""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.tracker import (
    DEFAULT_EVENT_TYPES,
    EVENT_PALETTE,
    EVENT_STYLES,
    ScheduleEvent,
    ScheduleEventType,
)


class EventTypeInUse(ValueError):
    """Удаление типа, на который ссылаются события, — его можно только скрыть."""


def _check(color: str, style: str) -> None:
    # Схема роута проверяет раньше; здесь страховка для скриптов и тестов.
    if color not in EVENT_PALETTE:
        raise ValueError(f"Unknown event color: {color}")
    if style not in EVENT_STYLES:
        raise ValueError(f"Unknown event style: {style}")


def list_types(db: Session, *, include_archived: bool = False) -> list[ScheduleEventType]:
    query = db.query(ScheduleEventType)
    if not include_archived:
        query = query.filter(ScheduleEventType.archived_at.is_(None))
    return query.order_by(
        ScheduleEventType.archived_at.isnot(None),
        ScheduleEventType.sort_order.asc(),
        ScheduleEventType.id.asc(),
    ).all()


def get_type(db: Session, type_id: int) -> ScheduleEventType | None:
    return db.get(ScheduleEventType, type_id)


def create_type(db: Session, *, name: str, color: str, style: str) -> ScheduleEventType:
    _check(color, style)
    last = db.query(func.max(ScheduleEventType.sort_order)).scalar()
    event_type = ScheduleEventType(
        name=name, color=color, style=style, sort_order=(last or 0) + 1
    )
    db.add(event_type)
    db.flush()
    return event_type


def update_type(event_type: ScheduleEventType, *, name: str, color: str, style: str) -> None:
    _check(color, style)
    event_type.name = name
    event_type.color = color
    event_type.style = style


def archive_type(event_type: ScheduleEventType) -> None:
    event_type.archived_at = datetime.now(timezone.utc)


def restore_type(event_type: ScheduleEventType) -> None:
    event_type.archived_at = None


def type_usage(db: Session) -> dict[int, int]:
    """{type_id: сколько событий на нём} — экран типов решает, удалить или скрыть."""
    rows = (
        db.query(ScheduleEvent.type_id, func.count(ScheduleEvent.id))
        .group_by(ScheduleEvent.type_id)
        .all()
    )
    return {type_id: count for type_id, count in rows}


def delete_type(db: Session, event_type: ScheduleEventType) -> None:
    used = (
        db.query(ScheduleEvent.id).filter(ScheduleEvent.type_id == event_type.id).first()
    )
    if used is not None:
        raise EventTypeInUse(event_type.id)
    db.delete(event_type)
    db.flush()


def move_type(db: Session, event_type: ScheduleEventType, direction: int) -> None:
    """Сдвиг на одну позицию среди видимых типов (−1 — выше, +1 — ниже).

    Порядок перенумеровывается целиком: у типов из миграции и созданных
    позже номера могли совпасть, обмен двух равных номеров ничего бы не сдвинул.
    """
    ordered = list_types(db)
    if event_type not in ordered:
        return
    index = ordered.index(event_type)
    target = index + (1 if direction > 0 else -1)
    if 0 <= target < len(ordered):
        ordered[index], ordered[target] = ordered[target], ordered[index]
    for position, item in enumerate(ordered):
        item.sort_order = position
    db.flush()


def seed_default_types(db: Session) -> None:
    """Стартовые типы для пустой базы — только стенд и тесты.

    На проде типы сеет миграция `0c4e9d2b7a61`. При старте приложения не
    зовётся: Главный преподаватель может скрыть или удалить все типы, и
    молчаливое возрождение их при перезапуске было бы сюрпризом.
    """
    if db.query(ScheduleEventType.id).first() is not None:
        return
    for index, (name, color, style) in enumerate(DEFAULT_EVENT_TYPES):
        db.add(ScheduleEventType(name=name, color=color, style=style, sort_order=index))
    db.flush()
