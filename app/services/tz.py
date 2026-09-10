"""Time-zone helpers. Все билеты и периоды активируются в 00:00 по Москве."""
from datetime import date, datetime, timezone
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


def parse_msk_local(raw: str | None) -> datetime | None:
    """Строка `datetime-local` (без таймзоны, с точностью до минут) — время
    трактуется как московское, результат — в UTC.

    Нужна там, где момент несёт не только дату, но и время (закрытие блока
    конструктора в 23:30, а не в полночь) — `msk_midnight` тут не подходит, она
    всегда даёт 00:00. Пусто или не парсится — `None`, вызывающий сам решает,
    ошибка это или «не задано» (тот же принцип, что у остальных полей
    `sync_blocks`: молча отбросить, а не уронить сохранение).
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=MSK_TZ)
    return value.astimezone(timezone.utc)
