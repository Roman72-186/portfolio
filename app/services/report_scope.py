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

**«Вход в продукт»** (Александрия 24.10.2026, владелец 29.10.2026) — третий
пункт переключателя только у «Статистики активности». Это не программа
аккаунта (`User.program` его не принимает), а срез годового курса: всё
правило годового учёта плюс тег «Новенькие» (`ANNUAL_NEWCOMER_TAG`, ставится
сам при заведении) и месяц прихода — по Москве месяц `program_access_from`
(у аккаунтов без отметки — `created_at`). Тег вечный, поэтому без месяца
новенькие октября и ноября смешались бы. После адаптации новенький
по-прежнему считается и в годовом курсе: срез его оттуда не забирает.

Не для карточки одного ученика: её открывают и у архивного, там считается
он сам (`activity_stats._scope_ids`). Не для очередей проверки, подбора
получателей и аудиторий заданий — это не учёт, у них свои правила.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import (
    ANNUAL_NEWCOMER_TAG, PROGRAM_ANNUAL, PROGRAM_HOLIDAY, PROGRAMS, REPORT_EXCLUDED_USER_IDS,
)
from app.models.role import Role
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.services.access_state import not_left_clause
from app.services.program import MONTH_NAMES
from app.services.tz import MSK_TZ


# Срез «Вход в продукт» — только для учёта, у аккаунта такой программы нет.
REPORT_NEWCOMERS = "newcomers"
REPORT_PROGRAMS = (*PROGRAMS, REPORT_NEWCOMERS)

_REPORT_PROGRAM: ContextVar[str] = ContextVar("report_program", default=PROGRAM_ANNUAL)
# Месяц прихода новеньких «ГГГГ-ММ» — только при `REPORT_NEWCOMERS`.
_REPORT_COHORT: ContextVar[str | None] = ContextVar("report_cohort", default=None)


@contextmanager
def report_program(program: str, cohort: str | None = None):
    """Программа учёта на время блока `with` — переключатель страницы
    статистики. Незнакомое значение — годовой курс. `cohort` — месяц прихода
    для «Входа в продукт»; без него срез пуст, а не «все новенькие»."""
    token = _REPORT_PROGRAM.set(program if program in REPORT_PROGRAMS else PROGRAM_ANNUAL)
    cohort_token = _REPORT_COHORT.set(cohort if program == REPORT_NEWCOMERS else None)
    try:
        yield
    finally:
        _REPORT_COHORT.reset(cohort_token)
        _REPORT_PROGRAM.reset(token)


def current_report_program() -> str:
    return _REPORT_PROGRAM.get()


REPORT_PROGRAM_LABELS = {
    PROGRAM_ANNUAL: "Годовой курс",
    PROGRAM_HOLIDAY: "Каникулярный курс",
    REPORT_NEWCOMERS: "Вход в продукт",
}


def parse_report_program(raw: str | None, *, newcomers: bool = False) -> str:
    """`newcomers` — страница знает срез «Вход в продукт» (пока только
    «Статистика активности»); остальным он — годовой курс."""
    allowed = REPORT_PROGRAMS if newcomers else PROGRAMS
    return raw if raw in allowed else PROGRAM_ANNUAL


def report_program_switch(path: str, program: str, *, newcomers: bool = False) -> dict:
    """Данные переключателя «Годовой курс / Каникулярный курс» над страницей
    статистики и экраном «Оплаты» — общий `partials/program_switch.html`."""
    values = REPORT_PROGRAMS if newcomers else PROGRAMS
    return {
        "current": program,
        "options": [
            {"value": value, "label": REPORT_PROGRAM_LABELS[value], "href": f"{path}?program={value}"}
            for value in values
        ],
    }


def _cohort_key(moment: datetime) -> str:
    if moment.tzinfo is None:  # SQLite в тестах отдаёт время без зоны — это UTC
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MSK_TZ).strftime("%Y-%m")


def _cohort_bounds(cohort: str) -> tuple[datetime, datetime] | None:
    """Месяц «ГГГГ-ММ» → [начало, начало следующего) по Москве."""
    try:
        year, month = (int(part) for part in cohort.split("-"))
        start = datetime(year, month, 1, tzinfo=MSK_TZ)
    except (AttributeError, ValueError):
        return None
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=MSK_TZ)
    # В UTC: так же сравнивают время остальные выборки (и SQLite в тестах).
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _newcomer_ids():
    """Подзапрос id учеников с тегом «Новенькие»."""
    return (
        select(UserTag.user_id)
        .join(Tag, Tag.id == UserTag.tag_id)
        .where(Tag.name == ANNUAL_NEWCOMER_TAG)
        .scalar_subquery()
    )


_ARRIVED_AT = func.coalesce(User.program_access_from, User.created_at)


def newcomer_cohorts(db: Session, now: datetime | None = None) -> list[dict]:
    """Месяцы прихода новеньких, что сейчас в годовом учёте, — новые первыми:
    `[{"value": "2026-10", "label": "Октябрь 2026", "count": 7}]`."""
    with report_program(PROGRAM_ANNUAL):
        rows = (
            reportable_students_q(db, now)
            .filter(User.id.in_(_newcomer_ids()))
            .with_entities(_ARRIVED_AT)
            .all()
        )
    counts: dict[str, int] = {}
    for (arrived_at,) in rows:
        if arrived_at is not None:
            key = _cohort_key(arrived_at)
            counts[key] = counts.get(key, 0) + 1
    return [
        {"value": key, "count": counts[key],
         "label": f"{MONTH_NAMES[int(key[5:]) - 1].capitalize()} {key[:4]}"}
        for key in sorted(counts, reverse=True)
    ]


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
    if _REPORT_PROGRAM.get() == REPORT_NEWCOMERS:
        bounds = _cohort_bounds(_REPORT_COHORT.get() or "")
        if bounds is None:
            return q.filter(False)
        return q.filter(
            User.program != PROGRAM_HOLIDAY,
            User.id.in_(_newcomer_ids()),
            _ARRIVED_AT >= bounds[0],
            _ARRIVED_AT < bounds[1],
        )
    return q if include_holiday else q.filter(User.program != PROGRAM_HOLIDAY)


def flow_students_q(db: Session):
    """Ученики годового курса за всё время, вместе с ушедшими, — для отчёта
    «кто пришёл, ушёл, перешёл» (`activity_stats.get_month_tariff_flow`).

    Обычная выборка ушедших скрывает: архивного, заблокированного и
    удалённого в ней нет и в прошлом, а отчёт месяца как раз о них. Здесь
    только постоянная часть правила: ранг ученика, не служебный, не
    каникулярный. Когда ушёл — отчёт решает сам по `archived_at`,
    `deleted_at` и журналу блокировок."""
    return (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),
            User.program != PROGRAM_HOLIDAY,
        )
    )


def reportable_student_ids(db: Session, now: datetime | None = None):
    """Подзапрос id для фильтра `col.in_(...)` по чужим таблицам (работы,
    циклы, оценки, события)."""
    return reportable_students_q(db, now).with_entities(User.id).scalar_subquery()


def reportable_student_id_set(db: Session, now: datetime | None = None) -> set[int]:
    """То же множеством — где аудитория уже собрана в памяти."""
    return {row[0] for row in reportable_students_q(db, now).with_entities(User.id).all()}
