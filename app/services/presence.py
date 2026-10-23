"""«Был на платформе» и время на платформе (Александрия 22.10.2026).

Открытая видимая вкладка ученика шлёт `POST /presence` сразу при открытии
страницы и потом раз в минуту (`static/js/presence.js`). Каждый пинг ставит
`User.last_seen_at` и прибавляет к дню (`UserActivityDay`, день по Москве)
время с прошлого пинга этого ученика.

Время считается промежутком между пингами, а не «+60 с за пинг»: две
открытые вкладки шлют вдвое больше пингов, и «+60 с» насчитало бы два часа
за час. Промежуток длиннее `PRESENCE_GAP_SECONDS` — перерыв, новый заход:
такой пинг ставит отметку и времени не прибавляет. Поэтому заход на одну
страницу без единого повтора пинга даёт ноль минут, а не минуту.

Кого считать — решает вызывающий (`api/auth.py::presence`: только ученик и
не вход «глазами»). Служебные пишутся как все, из цифр их убирает
`report_scope` — второго списка исключений здесь нет.

`last_seen_at` не входит в словарь сессии (`get_current_user`), поэтому
`invalidate_session` после записи не нужен: устаревать в кэше нечему.
"""
from datetime import date, datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.models.user import User, UserActivityDay
from app.services.tz import MSK_TZ

# Пинг раз в 60 с; запас на медленную сеть и усыплённый таймер вкладки.
PRESENCE_GAP_SECONDS = 120


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def record_presence(db: DBSession, user_id: int, now: datetime | None = None) -> None:
    """Отметить пинг ученика: `last_seen_at` и время за день."""
    now = now or datetime.now(timezone.utc)
    user = db.get(User, user_id)
    if user is None:
        return
    previous = _as_utc(user.last_seen_at)
    gap = (now - previous).total_seconds() if previous else None
    added = int(gap) if gap is not None and 0 < gap <= PRESENCE_GAP_SECONDS else 0
    user.last_seen_at = now

    day = now.astimezone(MSK_TZ).date()
    row = _day_row(db, user_id, day)
    if row is None:
        try:
            with db.begin_nested():
                row = UserActivityDay(
                    user_id=user_id, day=day, seconds=0, first_seen_at=now, last_seen_at=now,
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            # Вторая вкладка завела строку дня одновременно с этой.
            row = _day_row(db, user_id, day)
    row.seconds = (row.seconds or 0) + added
    row.last_seen_at = now
    db.commit()


def _day_row(db: DBSession, user_id: int, day: date) -> UserActivityDay | None:
    return (
        db.query(UserActivityDay)
        .filter(UserActivityDay.user_id == user_id, UserActivityDay.day == day)
        .first()
    )


def daily_seconds(db: DBSession, user_ids: list[int], *, since: date) -> dict[int, dict[str, int]]:
    """Секунды на платформе по дням с `since`: `{ученик: {"2026-10-26": 1800}}`.
    Для таблицы «По ученику», где период — выбранный в браузере цикл."""
    result: dict[int, dict[str, int]] = {}
    if not user_ids:
        return result
    for row in (
        db.query(UserActivityDay)
        .filter(UserActivityDay.user_id.in_(user_ids), UserActivityDay.day >= since)
    ):
        if row.seconds:
            result.setdefault(row.user_id, {})[row.day.isoformat()] = row.seconds
    return result


def seconds_on_platform(
    db: DBSession, user_ids: list[int], *, since: date, until: date | None = None,
) -> dict[int, int]:
    """Секунды на платформе по ученикам за дни `since`…`until` (по Москве,
    включительно; `until` пуст — по сегодня). Нет строк — ученика нет в ответе."""
    if not user_ids:
        return {}
    query = (
        db.query(UserActivityDay.user_id, func.sum(UserActivityDay.seconds))
        .filter(UserActivityDay.user_id.in_(user_ids), UserActivityDay.day >= since)
    )
    if until is not None:
        query = query.filter(UserActivityDay.day <= until)
    return {user_id: int(total or 0) for user_id, total in query.group_by(UserActivityDay.user_id)}
