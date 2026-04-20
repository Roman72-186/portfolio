"""Time-zone helpers. Все билеты и периоды активируются в 00:00 по Москве."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

MSK_TZ = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    """Текущее datetime в TZ Москва."""
    return datetime.now(MSK_TZ)


def today_msk() -> date:
    """Текущая дата в TZ Москва. Используется для фильтрации билетов и периодов
    «активен сегодня». Контейнер крутится в UTC, поэтому date.today() даст не то.
    """
    return now_msk().date()


def msk_midnight(d: date) -> datetime:
    """00:00 указанной даты в TZ Москва. Для сравнения с DateTime(timezone=True)
    колонками (например, Work.created_at)."""
    return datetime(d.year, d.month, d.day, tzinfo=MSK_TZ)
