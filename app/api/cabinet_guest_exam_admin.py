"""Staff-часть гостевого пробника (Трек B) — ВРЕМЕННЫЙ модуль, окно 26-28.08.2026.

Обычная авторизация (require_curator, rank >= 2) — куратор/админ выставляет балл
и комментарий один раз, без диалога проверки. См. app/services/guest_exam.py.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_csrf, require_curator
from app.models.guest_exam import GuestExamConfig, GuestParticipant, GuestSubmission
from app.services import guest_exam as guest_exam_service
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


@router.get("/staff/guest-exam", response_class=HTMLResponse)
def guest_exam_staff_list(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    # Модели проекта не используют relationship() — джойн вручную, по образцу
    # app/dependencies.py::get_current_user (Session, User).
    rows = (
        db.query(GuestSubmission, GuestParticipant)
        .join(GuestParticipant, GuestSubmission.participant_id == GuestParticipant.id)
        .filter(GuestSubmission.status.in_(["submitted", "scored"]))
        .order_by(GuestSubmission.status.asc(), GuestSubmission.submitted_at.desc())
        .all()
    )
    configs = {c.id: c for c in db.query(GuestExamConfig).all()}

    return templates.TemplateResponse("cabinet_guest_exam_staff.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "configs": configs,
    })


@router.post("/staff/guest-exam/{submission_id}/score")
def guest_exam_staff_score(
    request: Request,
    submission_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    score: Annotated[float, Form()],
    comment: Annotated[str, Form()] = "",
):
    submission = db.query(GuestSubmission).filter(GuestSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404)
    if submission.status == "issued":
        raise HTTPException(status_code=400, detail="Гость ещё не загрузил работу")

    guest_exam_service.score_submission(
        db, submission, score=score, comment=comment, scored_by_id=user["user_id"]
    )
    return RedirectResponse("/cabinet/staff/guest-exam", status_code=302)
