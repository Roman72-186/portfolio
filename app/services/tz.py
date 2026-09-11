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


def _as_utc(value: datetime) -> datetime:
    """Наивное время из базы считаем UTC. SQLite в тестах отдаёт datetime без
    таймзоны, и `astimezone` принял бы его за время машины — на европейском
    ноутбуке отсечка съехала бы на часы."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def msk_input_value(value: datetime | None) -> str:
    """Момент → строка для поля `datetime-local` в московском времени. Обратная
    операция к `parse_msk_local`: что показали в форме, то и примем назад."""
    if value is None:
        return ""
    return _as_utc(value).astimezone(MSK_TZ).strftime("%Y-%m-%dT%H:%M")


def msk_text(value: datetime | None) -> str:
    """Момент словами для человека: «27.09.2026 в 23:30» по Москве."""
    if value is None:
        return ""
    return _as_utc(value).astimezone(MSK_TZ).strftime("%d.%m.%Y в %H:%M")


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
