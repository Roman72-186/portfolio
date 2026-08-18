"""Staff-часть гостевого пробника (Трек B) — ВРЕМЕННЫЙ модуль, окно 26-28.08.2026.

Проверка сдач — обычная авторизация rank >= 2 (куратор ставит балл и комментарий
один раз, без диалога). Управление ссылкой и билетами — rank >= 4 (админ), той же
логикой, что и реальные билеты пробника: билет заполняется формой, фото уходит
через уже существующий AJAX-эндпоинт `/cabinet/upload-ticket-image`
(cabinet_superadmin.py) — свой загрузчик здесь не заводим.

См. app/services/guest_exam.py и
plans/2026-08-18-apparchi-student-cabinet-and-guest-trial.md, трек B.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf, require_curator
from app.models.guest_exam import GuestExamConfig, GuestParticipant, GuestSubmission, GuestTicket
from app.services import guest_exam as guest_exam_service
from app.services.tz import MSK_TZ, now_msk
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


def _parse_msk_local(raw: str, *, field_label: str) -> datetime:
    """datetime-local input (без TZ) трактуется как МСК, хранится в UTC — тот же
    приём, что и в cabinet_superadmin.py::_parse_msk_datetime_local для реальных
    билетов."""
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Неверное время «{field_label}»")
    if value.tzinfo is None:
        value = value.replace(tzinfo=MSK_TZ)
    return value.astimezone(timezone.utc)


# ── Проверка сдач ─────────────────────────────────────────────────────────────

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


# ── Ссылки (GuestExamConfig) и билеты — rank >= 4, та же логика, что у реальных
#    билетов пробника: форма + AJAX-загрузка фото ────────────────────────────

@router.get("/staff/guest-exam/configs", response_class=HTMLResponse)
def guest_exam_configs_list(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    configs = db.query(GuestExamConfig).order_by(GuestExamConfig.created_at.desc()).all()
    ticket_counts: dict[int, int] = {}
    if configs:
        for config_id, count in (
            db.query(GuestTicket.config_id, GuestTicket.id)
            .filter(GuestTicket.config_id.in_([c.id for c in configs]))
            .all()
        ):
            ticket_counts[config_id] = ticket_counts.get(config_id, 0) + 1

    return templates.TemplateResponse("cabinet_guest_exam_configs.html", {
        "request": request,
        "user": user,
        "configs": configs,
        "ticket_counts": ticket_counts,
        "default_starts": now_msk().strftime("%Y-%m-%dT%H:%M"),
        "default_ends": now_msk().strftime("%Y-%m-%dT%H:%M"),
    })


@router.post("/staff/guest-exam/configs")
def guest_exam_config_create(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    token: Annotated[str, Form()],
    title: Annotated[str, Form()],
    starts_at: Annotated[str, Form()],
    ends_at: Annotated[str, Form()],
):
    token_clean = token.strip().lower().replace(" ", "-")
    if not token_clean:
        raise HTTPException(status_code=422, detail="Укажите короткий код ссылки")
    if db.query(GuestExamConfig).filter(GuestExamConfig.token == token_clean).first():
        raise HTTPException(status_code=422, detail="Такой код ссылки уже занят")

    starts = _parse_msk_local(starts_at, field_label="начало приёма")
    ends = _parse_msk_local(ends_at, field_label="конец приёма")
    if ends <= starts:
        raise HTTPException(status_code=422, detail="Конец приёма должен быть позже начала")

    config = GuestExamConfig(
        token=token_clean,
        title=title.strip() or "Пробный экзамен",
        starts_at=starts,
        ends_at=ends,
        is_active=True,
        created_by_id=user["user_id"],
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return RedirectResponse(f"/cabinet/staff/guest-exam/configs/{config.id}", status_code=303)


@router.post("/staff/guest-exam/configs/{config_id}/toggle")
def guest_exam_config_toggle(
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
    return RedirectResponse(f"/cabinet/staff/guest-exam/configs/{config_id}", status_code=303)


@router.get("/staff/guest-exam/configs/{config_id}", response_class=HTMLResponse)
def guest_exam_config_detail(
    request: Request,
    config_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    config = db.query(GuestExamConfig).filter(GuestExamConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404)

    tickets = (
        db.query(GuestTicket)
        .filter(GuestTicket.config_id == config_id)
        .order_by(GuestTicket.subject, GuestTicket.created_at.desc())
        .all()
    )

    return templates.TemplateResponse("cabinet_guest_exam_config_detail.html", {
        "request": request,
        "user": user,
        "config": config,
        "tickets": tickets,
        "subjects": MOCK_SUBJECTS,
        "public_url": str(request.base_url).rstrip("/") + f"/guest/{config.token}",
    })


@router.post("/staff/guest-exam/configs/{config_id}/tickets")
def guest_exam_ticket_create(
    config_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    subject: Annotated[str, Form()],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    image_url: Annotated[str, Form()] = "",
    image_path: Annotated[str, Form()] = "",
):
    config = db.query(GuestExamConfig).filter(GuestExamConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404)
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=422, detail="Неверный предмет")
    if not title.strip():
        raise HTTPException(status_code=422, detail="Название билета обязательно")

    db.add(GuestTicket(
        config_id=config_id,
        subject=subject,
        title=title.strip(),
        description=description.strip() or None,
        image_s3_url=image_url.strip() or None,
        image_s3_path=image_path.strip() or None,
        is_active=True,
    ))
    db.commit()
    return RedirectResponse(f"/cabinet/staff/guest-exam/configs/{config_id}", status_code=303)


@router.post("/staff/guest-exam/tickets/{ticket_id}/toggle")
def guest_exam_ticket_toggle(
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
    return RedirectResponse(f"/cabinet/staff/guest-exam/configs/{ticket.config_id}", status_code=303)
