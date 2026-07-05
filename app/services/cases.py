from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session as DBSession

from app.constants import MONTH_TO_NUM
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM

CASE_GROWTH_THRESHOLD = 10.0


@dataclass(frozen=True)
class CaseGrowth:
    user_id: int
    subject: str
    previous_work_id: int
    current_work_id: int
    previous_score: float
    current_score: float
    growth: float
    previous_date: datetime | None
    current_date: datetime | None


@dataclass(frozen=True)
class CaseRow:
    student_id: int
    student_name: str
    tg_username: str
    tariff: str
    subject: str
    previous_work_id: int
    current_work_id: int
    previous_score: float
    current_score: float
    growth: float
    previous_date: datetime | None
    current_date: datetime | None
    previous_s3_url: str | None
    current_s3_url: str | None
    previous_filename: str
    current_filename: str


def _field(obj, name: str, default=None):
    try:
        return getattr(obj, name)
    except AttributeError:
        return default


def _score_value(raw: Decimal | float | int | None) -> float | None:
    if raw is None:
        return None
    return float(raw)


def _work_date(work) -> datetime | None:
    created_at = _field(work, "created_at")
    if created_at is not None:
        if created_at.tzinfo is None:
            return created_at.replace(tzinfo=timezone.utc)
        return created_at
    scored_at = _field(work, "scored_at")
    if scored_at is not None:
        if scored_at.tzinfo is None:
            return scored_at.replace(tzinfo=timezone.utc)
        return scored_at
    return None


def _work_sort_key(work) -> tuple:
    work_dt = _work_date(work) or datetime.min.replace(tzinfo=timezone.utc)
    month_num = MONTH_TO_NUM.get(_field(work, "month", "") or "", 0)
    return (
        _field(work, "year", 0) or 0,
        month_num,
        work_dt,
        _field(work, "id", 0) or 0,
    )


def _in_date_range(value: datetime | None, start_date: date | None, end_date: date | None) -> bool:
    if start_date is None and end_date is None:
        return True
    if value is None:
        return False
    value_date = value.date()
    if start_date is not None and value_date < start_date:
        return False
    if end_date is not None and value_date > end_date:
        return False
    return True


def find_case_growths_for_works(
    works: Iterable,
    *,
    min_growth: float = CASE_GROWTH_THRESHOLD,
    current_start_date: date | None = None,
    current_end_date: date | None = None,
) -> list[CaseGrowth]:
    """Find adjacent mock-exam score jumps by subject.

    A case is counted only when the current mock exam improved by at least
    `min_growth` points over the immediately previous checked mock exam of the
    same subject. Date filters apply to the current work in the pair, so an
    improvement inside the selected period can compare against an earlier work.
    """
    by_subject: dict[str, list] = {}
    for work in works:
        if _field(work, "work_type") != WORK_TYPE_MOCK_EXAM:
            continue
        subject = _field(work, "subject")
        score = _score_value(_field(work, "score"))
        if not subject or score is None:
            continue
        by_subject.setdefault(subject, []).append(work)

    cases: list[CaseGrowth] = []
    for subject, subject_works in by_subject.items():
        ordered = sorted(subject_works, key=_work_sort_key)
        previous = ordered[0] if ordered else None
        for current in ordered[1:]:
            if previous is None:
                previous = current
                continue
            previous_score = _score_value(getattr(previous, "score", None))
            current_score = _score_value(getattr(current, "score", None))
            current_date = _work_date(current)
            if (
                previous_score is not None
                and current_score is not None
                and current_score - previous_score >= min_growth
                and _in_date_range(current_date, current_start_date, current_end_date)
            ):
                cases.append(
                    CaseGrowth(
                        user_id=_field(current, "user_id", 0) or 0,
                        subject=subject,
                        previous_work_id=_field(previous, "id", 0) or 0,
                        current_work_id=_field(current, "id", 0) or 0,
                        previous_score=previous_score,
                        current_score=current_score,
                        growth=current_score - previous_score,
                        previous_date=_work_date(previous),
                        current_date=current_date,
                    )
                )
            previous = current
    return cases


def has_case_growth(works: Iterable, *, min_growth: float = CASE_GROWTH_THRESHOLD) -> bool:
    return bool(find_case_growths_for_works(works, min_growth=min_growth))


def _student_display_name(student: User) -> str:
    return f"{student.last_name or ''} {student.first_name or student.name}".strip()


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time.max).replace(tzinfo=timezone.utc)


def build_case_rows(
    db: DBSession,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    subject: str = "",
) -> list[CaseRow]:
    query = (
        db.query(Work, User)
        .join(User, Work.user_id == User.id)
        .filter(
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.status == "success",
            Work.score.isnot(None),
            Work.subject.isnot(None),
            User.deleted_at.is_(None),
            User.is_active == True,  # noqa: E712
        )
    )
    if subject:
        query = query.filter(Work.subject == subject)
    if end_date is not None:
        query = query.filter(Work.created_at <= _end_of_day(end_date))

    rows = query.all()
    works_by_user: dict[int, list[Work]] = {}
    students_by_id: dict[int, User] = {}
    works_by_id: dict[int, Work] = {}
    for work, student in rows:
        works_by_user.setdefault(student.id, []).append(work)
        students_by_id[student.id] = student
        works_by_id[work.id] = work

    result: list[CaseRow] = []
    for student_id, works in works_by_user.items():
        student = students_by_id[student_id]
        for case in find_case_growths_for_works(
            works,
            current_start_date=start_date,
            current_end_date=end_date,
        ):
            previous = works_by_id[case.previous_work_id]
            current = works_by_id[case.current_work_id]
            result.append(
                CaseRow(
                    student_id=student.id,
                    student_name=_student_display_name(student),
                    tg_username=(student.tg_username or "").lstrip("@"),
                    tariff=student.tariff or "",
                    subject=case.subject,
                    previous_work_id=case.previous_work_id,
                    current_work_id=case.current_work_id,
                    previous_score=case.previous_score,
                    current_score=case.current_score,
                    growth=case.growth,
                    previous_date=case.previous_date,
                    current_date=case.current_date,
                    previous_s3_url=previous.s3_url,
                    current_s3_url=current.s3_url,
                    previous_filename=previous.filename,
                    current_filename=current.filename,
                )
            )

    return sorted(
        result,
        key=lambda row: (
            row.current_date or datetime.min.replace(tzinfo=timezone.utc),
            row.growth,
            row.student_name.lower(),
        ),
        reverse=True,
    )
