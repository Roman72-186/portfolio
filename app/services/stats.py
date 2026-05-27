"""Aggregated statistics for staff dashboards (admin/superadmin/curator)."""
from datetime import datetime, timedelta, timezone
from typing import Optional, TypedDict

from sqlalchemy import func, extract
from sqlalchemy.orm import Session as DBSession

from app.constants import FEATURE_MOCK_EXAM, MOCK_SUBJECTS
from app.models.feature_period import FeaturePeriod
from app.models.role import Role
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.feature_periods import get_active_period
from app.services.tz import msk_midnight


class SubjectStat(TypedDict):
    subject: str
    scored: int   # сколько учеников получили оценку за период
    total: int    # сколько учеников всего попадает в период


class CurvePoint(TypedDict):
    label: str           # "март 2026"
    year: int
    month: int           # 1..12
    drawing: Optional[float]   # средний балл по Рисунку, None если нет данных
    composition: Optional[float]


_MONTH_LABELS_RU = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def _student_total(db: DBSession, curator_id: Optional[int] = None) -> int:
    """Кол-во активных учеников (rank=1). При curator_id — только закреплённых за куратором."""
    q = (
        db.query(func.count(User.id))
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 1, User.is_active == True)
    )
    if curator_id is not None:
        q = q.filter(User.curator_id == curator_id)
    return q.scalar() or 0


def mock_period_subject_stats(
    db: DBSession,
    curator_id: Optional[int] = None,
) -> tuple[Optional[FeaturePeriod], list[SubjectStat]]:
    """
    Для последнего активного периода пробников: сколько учеников получили оценку
    по каждому предмету (Рисунок / Композиция).

    «Получил оценку» = у ученика есть Work(mock_exam, score IS NOT NULL, subject=<…>)
    за окно периода [start_date, end_date+1] MSK.

    Если curator_id задан, ограничиваем учениками куратора.
    Возвращает (period, stats). period=None если активного периода нет.
    """
    period = get_active_period(db, FEATURE_MOCK_EXAM)
    total = _student_total(db, curator_id)

    if period is None:
        return None, [
            {"subject": subj, "scored": 0, "total": total}
            for subj in MOCK_SUBJECTS
        ]

    start = msk_midnight(period.start_date)
    end = msk_midnight(period.end_date + timedelta(days=1))

    base = (
        db.query(Work.subject, func.count(func.distinct(Work.user_id)))
        .filter(
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.score.isnot(None),
            Work.subject.in_(MOCK_SUBJECTS),
            Work.created_at >= start,
            Work.created_at < end,
        )
    )
    if curator_id is not None:
        base = base.join(User, Work.user_id == User.id).filter(
            User.curator_id == curator_id,
            User.is_active == True,
        )

    rows = base.group_by(Work.subject).all()
    by_subject = {subj: int(cnt) for subj, cnt in rows}

    stats: list[SubjectStat] = [
        {"subject": subj, "scored": by_subject.get(subj, 0), "total": total}
        for subj in MOCK_SUBJECTS
    ]
    return period, stats


def score_curve_12m(db: DBSession) -> list[CurvePoint]:
    """
    Средний балл по mock_exam за последние 12 месяцев, разрез по предмету (Рисунок / Композиция).
    Группировка по году+месяцу `Work.created_at` (UTC → не критично для агрегата).
    Возвращает 12 точек, отсортированных от старого к новому. Если в месяце нет данных по
    предмету — соответствующее поле = None (на графике это разрыв).
    """
    now = datetime.now(timezone.utc)
    # начало окна: 1-е число месяца, 11 месяцев назад → итого 12 баков
    year = now.year
    month = now.month - 11
    while month <= 0:
        month += 12
        year -= 1
    window_start = datetime(year, month, 1, tzinfo=timezone.utc)

    year_col = extract("year", Work.created_at)
    month_col = extract("month", Work.created_at)

    rows = (
        db.query(
            year_col.label("y"),
            month_col.label("m"),
            Work.subject,
            func.avg(Work.score).label("avg_score"),
        )
        .filter(
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.score.isnot(None),
            Work.subject.in_(MOCK_SUBJECTS),
            Work.created_at >= window_start,
        )
        .group_by(year_col, month_col, Work.subject)
        .all()
    )

    bucket: dict[tuple[int, int], dict[str, Optional[float]]] = {}
    for r in rows:
        key = (int(r.y), int(r.m))
        slot = bucket.setdefault(key, {"Рисунок": None, "Композиция": None})
        if r.subject in MOCK_SUBJECTS and r.avg_score is not None:
            slot[r.subject] = round(float(r.avg_score), 1)

    points: list[CurvePoint] = []
    cy, cm = year, month
    for _ in range(12):
        slot = bucket.get((cy, cm), {})
        points.append({
            "label": f"{_MONTH_LABELS_RU[cm]} {cy}",
            "year": cy,
            "month": cm,
            "drawing": slot.get("Рисунок"),
            "composition": slot.get("Композиция"),
        })
        cm += 1
        if cm > 12:
            cm = 1
            cy += 1
    return points


def curator_avg_scores(
    db: DBSession,
    curator_id: int,
) -> dict[str, Optional[float]]:
    """
    Средний балл по mock_exam, разрез по предмету, по всем ученикам куратора, за всё время.
    Возвращает {"Рисунок": 67.4, "Композиция": None}.
    """
    rows = (
        db.query(Work.subject, func.avg(Work.score))
        .join(User, Work.user_id == User.id)
        .filter(
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.score.isnot(None),
            Work.subject.in_(MOCK_SUBJECTS),
            User.curator_id == curator_id,
        )
        .group_by(Work.subject)
        .all()
    )
    by_subject: dict[str, Optional[float]] = {subj: None for subj in MOCK_SUBJECTS}
    for subj, avg in rows:
        if avg is not None:
            by_subject[subj] = round(float(avg), 1)
    return by_subject
