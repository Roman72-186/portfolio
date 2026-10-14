"""Кого считать в статистике — одно правило на все страницы.

Владелец 07.10.2026: «не учитывать заблокированных, удалённых, только с
активной подпиской» — везде, где сотрудник видит цифры по ученикам, и вместе
с историей: ушедший ученик пропадает и из прошлых метрик (скорость проверки,
оценки ОС, счётчики сдач), потому что фильтр — по текущему состоянию аккаунта.

Ученик в учёте, если одновременно:
- роль ученика (ранг 1);
- `is_active` — блокировка ставит False;
- `archived_at` пуст — архив прошлого потока; он тоже снимает `is_active`,
  но это отдельное состояние со своей колонкой, проверяется явно;
- `deleted_at` пуст;
- подписка активна: ни пробный срок (`access_until`), ни оплаченный
  (`paid_until`, при включённой блокировке) не наступили — то же правило,
  что запирает кабинет, `services/access_state.py`;
- не служебный аккаунт (`REPORT_EXCLUDED_USER_IDS`);
- годовой курс: каникулярный набор (`User.program == 'holiday'`, созвон
  13.10.2026) — внешние ученики на неделю, отдельный список, в цифры школы
  не входит. Исключение — статистика одного цикла (`cycle_stats`,
  `include_holiday=True`): аудитория цикла уже из одной программы, и у
  каникулярного цикла без набора не осталось бы никого.

До 07.10.2026 каждый сервис статистики отбирал учеников сам, и отборы
расходились: одни отсекали только служебных, другие ещё и неактивных, срок
подписки не смотрел никто. Новая выборка учеников для статистики берётся
отсюда; своя копия условий — тот же разъезд заново.

Не для карточки одного ученика: её открывают и у архивного, там считается
он сам (`activity_stats._scope_ids`). Не для очередей проверки, подбора
получателей и аудиторий заданий — это не учёт, у них свои правила.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.constants import PROGRAM_HOLIDAY, REPORT_EXCLUDED_USER_IDS
from app.models.role import Role
from app.models.user import User
from app.services.access_state import access_open_clause


def reportable_students_q(
    db: Session, now: datetime | None = None, *, include_holiday: bool = False,
):
    """Запрос `User` по ученикам в учёте; к нему можно добавлять фильтры."""
    now = now or datetime.now(timezone.utc)
    q = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.is_active == True,  # noqa: E712
            User.archived_at.is_(None),
            User.deleted_at.is_(None),
            access_open_clause(now),
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),
        )
    )
    return q if include_holiday else q.filter(User.program != PROGRAM_HOLIDAY)


def reportable_student_ids(db: Session, now: datetime | None = None):
    """Подзапрос id для фильтра `col.in_(...)` по чужим таблицам (работы,
    циклы, оценки, события)."""
    return reportable_students_q(db, now).with_entities(User.id).scalar_subquery()


def reportable_student_id_set(db: Session, now: datetime | None = None) -> set[int]:
    """То же множеством — где аудитория уже собрана в памяти."""
    return {row[0] for row in reportable_students_q(db, now).with_entities(User.id).all()}
