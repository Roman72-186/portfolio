"""Гостевой режим — админ-панель (Трек B), ВРЕМЕННЫЙ модуль.

Отдельная кнопка «Гостевой режим» только у админа и суперадмина (rank >= 4), одна
страница с тремя вкладками: Билеты (форма создания — та же логика, что у реальных
билетов пробника: поля + фото через уже существующий /cabinet/upload-ticket-image),
Ссылка (бессрочная, только вкл/выкл вручную + статистика входов), Работы
(проверка сдач — балл и комментарий один раз, без диалога).

Вкладки «Билеты»/«Работы» работают с «текущей» ссылкой — активной, либо
последней созданной, если активных нет (см. guest_exam_service.get_primary_config).

См. app/services/guest_exam.py и
plans/2026-08-18-apparchi-student-cabinet-and-guest-trial.md, трек B.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf
from app.models.guest_exam import GuestExamConfig, GuestParticipant, GuestSubmission, GuestTicket
from app.services import guest_exam as guest_exam_service
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/guest-exam")


@router.get("", response_class=HTMLResponse)
def guest_mode_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    tab: str = "tickets",
):
    if tab not in ("tickets", "link", "works"):
        tab = "tickets"

    config = guest_exam_service.get_primary_config(db)
    configs = db.query(GuestExamConfig).order_by(GuestExamConfig.created_at.desc()).all()
    stats_by_config = {c.id: guest_exam_service.config_stats(db, c.id) for c in configs}

    tickets = []
    if config:
        tickets = (
            db.query(GuestTicket)
            .filter(GuestTicket.config_id == config.id)
            .order_by(GuestTicket.subject, GuestTicket.created_at.desc())
            .all()
        )

    rows = (
        db.query(GuestSubmission, GuestParticipant)
        .join(GuestParticipant, GuestSubmission.participant_id == GuestParticipant.id)
        .filter(GuestSubmission.status.in_(["submitted", "scored"]))
        .order_by(GuestSubmission.status.asc(), GuestSubmission.submitted_at.desc())
        .all()
    )

    return templates.TemplateResponse("cabinet_guest_mode.html", {
        "request": request,
        "user": user,
        "tab": tab,
        "config": config,
        "configs": configs,
        "stats_by_config": stats_by_config,
        "tickets": tickets,
        "subjects": MOCK_SUBJECTS,
        "rows": rows,
        "public_url": (
            str(request.base_url).rstrip("/") + f"/guest/{config.token}" if config else None
        ),
    })


# ── Вкладка «Ссылка» ─────────────────────────────────────────────────────────

@router.post("/link")
def guest_mode_create_link(
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    token: Annotated[str, Form()],
    title: Annotated[str, Form()],
):
    token_clean = token.strip().lower().replace(" ", "-")
    if not token_clean:
        raise HTTPException(status_code=422, detail="Укажите короткий код ссылки")
    if db.query(GuestExamConfig).filter(GuestExamConfig.token == token_clean).first():
        raise HTTPException(status_code=422, detail="Такой код ссылки уже занят")

    db.add(GuestExamConfig(
        token=token_clean,
        title=title.strip() or "Пробный экзамен",
        is_active=True,
        created_by_id=user["user_id"],
    ))
    db.commit()
    return RedirectResponse("/cabinet/staff/guest-exam?tab=link", status_code=303)


@router.post("/link/{config_id}/toggle")
def guest_mode_toggle_link(
    config_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    config = db.query(GuestExamConfig).filter(GuestExamConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404)
    config.is_active = not config.is_active
    db.commit()
    return RedirectResponse("/cabinet/staff/guest-exam?tab=link", status_code=303)


# ── Вкладка «Билеты» ─────────────────────────────────────────────────────────

@router.post("/tickets")
def guest_mode_create_ticket(
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    subject: Annotated[str, Form()],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    image_url: Annotated[str, Form()] = "",
    image_path: Annotated[str, Form()] = "",
):
    config = guest_exam_service.get_primary_config(db)
    if not config:
        raise HTTPException(status_code=422, detail="Сначала создайте ссылку на вкладке «Ссылка»")
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=422, detail="Неверный предмет")
    if not title.strip():
        raise HTTPException(status_code=422, detail="Название билета обязательно")

    db.add(GuestTicket(
        config_id=config.id,
        subject=subject,
        title=title.strip(),
        description=description.strip() or None,
        image_s3_url=image_url.strip() or None,
        image_s3_path=image_path.strip() or None,
        is_active=True,
    ))
    db.commit()
    return RedirectResponse("/cabinet/staff/guest-exam?tab=tickets", status_code=303)


@router.post("/tickets/{ticket_id}/toggle")
def guest_mode_toggle_ticket(
    ticket_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    ticket = db.query(GuestTicket).filter(GuestTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404)
    ticket.is_active = not ticket.is_active
    db.commit()
    return RedirectResponse("/cabinet/staff/guest-exam?tab=tickets", status_code=303)


# ── Вкладка «Работы» ─────────────────────────────────────────────────────────

@router.post("/works/{submission_id}/score")
def guest_mode_score(
    submission_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
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
    return RedirectResponse("/cabinet/staff/guest-exam?tab=works", status_code=303)
