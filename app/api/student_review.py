"""Единый экран проверки: куратор открывает ученика и разбирает всё, что тот
сдал, за один заход — вместо очереди «по одному типу задания сразу через
весь список учеников» (созвон 01.09.2026,
`plans/2026-09-01-apparchi-student-centric-review.md`).

Решения владельца: экран полный уже для куратора (rank 2) — права на балл
Work/ExamCycle расширены отдельно (`cabinet_students_shared.py::score_work`,
`feedback.py::close_cycle_route`); канонический список учеников —
`_accessible_students` (тот же приём, что `_get_accessible_students`); период
— календарная неделя; диалог — только ссылка на существующий UI, здесь не
встраивается; сводного балла на этом экране нет (отдельная стройка).
"""

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS, TARIFFS
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_curator
from app.models.exam_cycle import ExamCycle
from app.models.work import Work
from app.services.review_aggregate import (
    aggregate_student_review_counts,
    student_review_items,
    week_bounds,
)
from app.services.student_access import get_student_for_staff_access
from app.services.tz import today_msk
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/students-review")

# С этого ранга видно всех учеников без ограничения по curator_id.
FULL_ACCESS_RANK = 4


def _curator_scope(user: dict) -> int | None:
    return None if user.get("role_rank", 0) >= FULL_ACCESS_RANK else user["user_id"]


def _parse_week(week: str | None) -> date:
    if not week:
        return today_msk()
    try:
        return datetime.strptime(week, "%Y-%m-%d").date()
    except ValueError:
        return today_msk()


@router.get("", response_class=HTMLResponse)
def students_review_list(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    rows = aggregate_student_review_counts(db, user)
    return templates.TemplateResponse("staff_students_review.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "nav_active": "students_review",
    })


@router.get("/{student_id}", response_class=HTMLResponse)
def student_review_detail(
    student_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    week: str | None = None,
    subject: str | None = None,
    tariff: str | None = None,
):
    student = get_student_for_staff_access(
        db, user, student_id,
        active_only=True,
        not_found_detail="Ученик не найден",
        forbidden_detail="Нет доступа к этому ученику",
    )
    anchor = _parse_week(week)
    week_start, week_end = week_bounds(anchor)
    prev_week = (week_start.date() - (week_end.date() - week_start.date()))
    next_week = week_end.date()

    items = student_review_items(
        db,
        student_id=student_id,
        curator_id=_curator_scope(user),
        week_start=week_start,
        week_end=week_end,
        subject=subject or None,
        tariff=tariff or None,
    )
    return templates.TemplateResponse("staff_student_review_detail.html", {
        "request": request,
        "user": user,
        "student": student,
        "items": items,
        "week_start": week_start.date(),
        "week_end": week_end.date(),
        "prev_week": prev_week.isoformat(),
        "next_week": next_week.isoformat(),
        "subject": subject or "",
        "tariff": tariff or "",
        "subjects": MOCK_SUBJECTS,
        "tariffs": TARIFFS,
        "nav_active": "students_review",
    })


# ── Действия прямо с экрана (этап 6) ────────────────────────────────────────
#
# «Просмотрено» только отмечает и не снимает отметку — в отличие от
# TaskBlockAnswer (там снятие нужно: ткнули случайно на входящей очереди),
# здесь строка с балл/закрытием уже выходит из непроверенных сама, отдельная
# кнопка «просмотрено» нужна только чтобы добавить признак, не убрать чужой.


@router.post("/work/{work_id}/viewed", response_class=JSONResponse)
def mark_work_viewed(
    work_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    get_student_for_staff_access(
        db, user, work.user_id,
        not_found_detail="Работа не найдена",
        forbidden_detail="Это не ваш студент",
    )
    work.viewed_at = work.viewed_at or datetime.now(timezone.utc)
    work.viewed_by_id = user["user_id"]
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/cycle/{cycle_id}/viewed", response_class=JSONResponse)
def mark_cycle_viewed(
    cycle_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    cycle = db.get(ExamCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    get_student_for_staff_access(
        db, user, cycle.user_id,
        not_found_detail="Цикл не найден",
        forbidden_detail="Это не ваш студент",
    )
    cycle.viewed_at = cycle.viewed_at or datetime.now(timezone.utc)
    cycle.viewed_by_id = user["user_id"]
    db.commit()
    return JSONResponse({"ok": True})
