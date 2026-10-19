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
- не ушёл с курса: срок доступа (`access_until`) пуст или впереди
  (`access_state.not_left_clause`). Оплаченный срок (`paid_until`) учёт не
  смотрит: владелец 16.10.2026 — «везде… только убирать ушедших»; должник,
  которому кабинет закрыт за неоплату, остаётся в цифрах школы, пока не
  ушёл. До 16.10 здесь стояло полное правило замка кабинета
  (`access_open_clause`), и закрытые за неоплату выпадали из статистики;
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

**Программа учёта** (владелец 19.10.2026: «в статистике… переключатель —
годовой курс и каникулярный курс»). По умолчанию — годовой курс, как
описано выше. Страница статистики на время своего запроса включает
каникулярный (`with report_program(PROGRAM_HOLIDAY):`), и тогда в учёте
каникулярные ученики: роль ученика, активные, не в архиве, не удалённые, не
служебные — **без отсечки по сроку доступа**: набор длится неделю, и после
1.11 его история должна оставаться видна. Так все карточки, которые считают
через это правило, переключаются сразу, без своей копии условий. Программа
живёт в `ContextVar` и сбрасывается по выходу из `with`: в остальных
запросах учёт годовой.

Не для карточки одного ученика: её открывают и у архивного, там считается
он сам (`activity_stats._scope_ids`). Не для очередей проверки, подбора
получателей и аудиторий заданий — это не учёт, у них свои правила.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.constants import PROGRAM_ANNUAL, PROGRAM_HOLIDAY, PROGRAMS, REPORT_EXCLUDED_USER_IDS
from app.models.role import Role
from app.models.user import User
from app.services.access_state import not_left_clause


_REPORT_PROGRAM: ContextVar[str] = ContextVar("report_program", default=PROGRAM_ANNUAL)


@contextmanager
def report_program(program: str):
    """Программа учёта на время блока `with` — переключатель страницы
    статистики. Незнакомое значение — годовой курс."""
    token = _REPORT_PROGRAM.set(program if program in PROGRAMS else PROGRAM_ANNUAL)
    try:
        yield
    finally:
        _REPORT_PROGRAM.reset(token)


def current_report_program() -> str:
    return _REPORT_PROGRAM.get()


REPORT_PROGRAM_LABELS = {PROGRAM_ANNUAL: "Годовой курс", PROGRAM_HOLIDAY: "Каникулярный курс"}


def parse_report_program(raw: str | None) -> str:
    return raw if raw in PROGRAMS else PROGRAM_ANNUAL


def report_program_switch(path: str, program: str) -> dict:
    """Данные переключателя «Годовой курс / Каникулярный курс» над страницей
    статистики и экраном «Оплаты» — общий `partials/program_switch.html`."""
    return {
        "current": program,
        "options": [
            {"value": value, "label": REPORT_PROGRAM_LABELS[value], "href": f"{path}?program={value}"}
            for value in PROGRAMS
        ],
    }


def reportable_students_q(
    db: Session, now: datetime | None = None, *, include_holiday: bool = False,
):
    """Запрос `User` по ученикам в учёте; к нему можно добавлять фильтры."""
    now = now or datetime.now(timezone.utc)
    if _REPORT_PROGRAM.get() == PROGRAM_HOLIDAY:
        return (
            db.query(User)
            .join(Role, User.role_id == Role.id)
            .filter(
                Role.rank == 1,
                User.program == PROGRAM_HOLIDAY,
                User.is_active == True,  # noqa: E712
                User.archived_at.is_(None),
                User.deleted_at.is_(None),
                User.id.notin_(REPORT_EXCLUDED_USER_IDS),
            )
        )
    q = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.is_active == True,  # noqa: E712
            User.archived_at.is_(None),
            User.deleted_at.is_(None),
            not_left_clause(now),
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
