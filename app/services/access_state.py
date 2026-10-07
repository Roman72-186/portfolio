"""Закрыт ли кабинет ученика по сроку — одно правило на вход и на выборки.

Срока два, и значат они разное:

- `User.access_until` — срок, который ставит куратор или вход по `/proba`.
  Непустой он ещё и признак новичка пробного набора (лента, блоки, анкета),
  поэтому оплата его не трогает;
- `User.paid_until` — до какого момента оплачено обучение
  (`services/payments.py`). Держит кабинет, только когда включена блокировка
  по неоплате (`settings.payments_block_enabled`): первый месяц заказчик
  сначала смотрит список должников.

Наступил любой — открыта одна «Личная информация» (`get_current_user`), и
ученик выпадает из учёта (`report_scope`) и из напоминаний
(`tracker.reminder_recipients`). До 07.10.2026 условие по `access_until` жило
копией в каждом из трёх мест; новый срок добавлялся бы в три места, и одно
отстало бы.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, true

from app.config import settings
from app.models.user import User
from app.services.tz import _as_utc


def access_expired(user: User, now: datetime | None = None) -> bool:
    """Истёк ли у этого аккаунта срок доступа. Роль не смотрит — срок держит
    только учеников, это проверяет вызывающий."""
    now = now or datetime.now(timezone.utc)
    if user.access_until is not None and _as_utc(user.access_until) <= now:
        return True
    if (
        settings.payments_block_enabled
        and user.paid_until is not None
        and _as_utc(user.paid_until) <= now
    ):
        return True
    return False


def access_open_clause(now: datetime):
    """То же условием для запроса по `User`: доступ ещё открыт."""
    paid_clause = (
        or_(User.paid_until.is_(None), User.paid_until > now)
        if settings.payments_block_enabled
        else true()
    )
    return or_(User.access_until.is_(None), User.access_until > now) & paid_clause
