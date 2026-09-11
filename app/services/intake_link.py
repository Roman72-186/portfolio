"""Ссылка пробного набора предобучения (`app/models/intake_link.py`).

Одна модель, одна строка на slug — кэш не нужен, запрос идёт по уникальному
индексу.
"""
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app.models.intake_link import IntakeLink


def get_link(db: DBSession, slug: str) -> IntakeLink | None:
    return db.query(IntakeLink).filter(IntakeLink.slug == slug).first()


def is_open(link: IntakeLink | None) -> bool:
    """Ссылка работает как вход прямо сейчас: включена и дата задана."""
    return bool(link and link.is_active and link.access_until is not None)


def deadline_for_slug(db: DBSession, slug: str) -> datetime | None:
    """Срок доступа, который получит новый пользователь, пришедший по этой
    ссылке — независимо от `is_active`.

    Между кликом по ссылке и возвратом из Telegram проходит до 10 минут;
    выключи владелец ссылку в это окно — отдать человеку бессрочный доступ
    хуже, чем поставить срок.
    """
    link = get_link(db, slug)
    return link.access_until if link else None
