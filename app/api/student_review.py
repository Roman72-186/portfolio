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
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS, TARIFFS
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_curator
from app.models.exam_cycle import ExamCycle
from app.models.task_block import TaskBlockAnswer, TaskBlockResponse
from app.models.user import User
from app.models.work import Work
from app.services.review_aggregate import (
    FULL_ACCESS_RANK,
    aggregate_student_review_counts,
    student_review_items,
    week_bounds,
)
from app.services.task_blocks import set_reviewed
from app.services.tz import today_msk
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/students-review")


def _curator_scope(user: dict) -> int | None:
    return None if user.get("role_rank", 0) >= FULL_ACCESS_RANK else user["user_id"]


def _check_student_access(
    db: DBSession, user: dict, student_id: int, *, not_found_detail: str, forbidden_detail: str,
) -> User:
    """rank < FULL_ACCESS_RANK (куратор и модератор) — только свои ученики.

    Не `get_student_for_staff_access`: её owner-проверка срабатывает только
    при `role_rank == 2`, а сюда пускает `require_curator` (rank ≥ 2) — без
    этой явной проверки модератор видел бы чужих учеников (advisor-ревью
    01.09.2026)."""
    student = db.query(User).filter(User.id == student_id, User.is_active == True).first()  # noqa: E712
    if student is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    if user["role_rank"] < FULL_ACCESS_RANK and student.curator_id != user["user_id"]:
        raise HTTPException(status_code=403, detail=forbidden_detail)
    return student


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
    student = _check_student_access(
        db, user, student_id,
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
    _check_student_access(
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
    _check_student_access(
        db, user, cycle.user_id,
        not_found_detail="Цикл не найден",
        forbidden_detail="Это не ваш студент",
    )
    cycle.viewed_at = cycle.viewed_at or datetime.now(timezone.utc)
    cycle.viewed_by_id = user["user_id"]
    db.commit()
    return JSONResponse({"ok": True})


class TaskBlockReviewMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: bool = True


@router.post("/task-block/{answer_id}/reviewed", response_class=JSONResponse)
def mark_task_block_reviewed(
    answer_id: int,
    payload: TaskBlockReviewMark,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Тумблер «Просмотрено» для ответа на блок задания (снос отдельного
    экрана `/cabinet/staff/review` 02.09.2026, там кнопка была такая же).

    Владелец ученика ответа берём из `TaskBlockResponse.user_id`, а не из
    тела запроса — иначе куратор мог бы подставить своего ученика и снять/
    поставить отметку на чужом ответе (тот же класс дыры, что чинили в
    `_check_student_access` 01.09.2026)."""
    row = (
        db.query(TaskBlockAnswer, TaskBlockResponse)
        .join(TaskBlockResponse, TaskBlockResponse.id == TaskBlockAnswer.response_id)
        .filter(TaskBlockAnswer.id == answer_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ответ не найден")
    _answer, response = row
    _check_student_access(
        db, user, response.user_id,
        not_found_detail="Ответ не найден",
        forbidden_detail="Это не ваш студент",
    )
    answer = set_reviewed(
        db, answer_id=answer_id, user_id=user["user_id"], reviewed=payload.reviewed
    )
    db.commit()
    return JSONResponse({"ok": True, "reviewed": answer.reviewed_at is not None})
