"""Публичные роуты гостевого режима (Трек B) — ВРЕМЕННЫЙ модуль, пробник для
участников без регистрации.

Намеренно не использует Depends(get_current_user)/require_student — гость не
проходит через основную auth-систему вообще: свой cookie, своя CSRF-привязка.
Ссылка бессрочная — единственный гейт доступа GuestExamConfig.is_active.
См. app/services/guest_exam.py и
plans/2026-08-18-apparchi-student-cabinet-and-guest-trial.md, трек B.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.auth import TG_PURPOSE_GUEST, telegram_login_enabled, telegram_oauth_redirect
from app.constants import MOCK_SUBJECTS
from app.csrf import generate_csrf_token, validate_csrf_token
from app.db.database import get_db
from app.limiter import limiter
from app.services import guest_exam as guest_exam_service
from app.services import s3 as s3_service
from app.services.upload_validation import read_image_uploads
from app.services.utils import compress_image
from app.tmpl import templates

router = APIRouter()

GUEST_COOKIE_NAME = guest_exam_service.GUEST_COOKIE_NAME


def _get_config_or_404(db: DBSession, token: str):
    config = guest_exam_service.get_config_by_token(db, token)
    if not config:
        raise HTTPException(status_code=404)
    return config


def _get_participant_from_cookie(request: Request, db: DBSession, config):
    raw = request.cookies.get(GUEST_COOKIE_NAME)
    if not raw:
        return None
    payload = guest_exam_service.load_guest_cookie(raw)
    if not payload or payload.get("config_token") != config.token:
        return None
    return guest_exam_service.get_participant(db, payload.get("participant_id"), config.id)


def require_guest_csrf(
    request: Request,
    csrf_token: Annotated[str, Form(alias="csrf_token")] = "",
) -> None:
    raw = request.cookies.get(GUEST_COOKIE_NAME, "")
    if not validate_csrf_token(raw, csrf_token):
        raise HTTPException(
            status_code=403,
            detail="Неверный CSRF-токен. Обновите страницу и попробуйте снова.",
        )


@router.get("/guest/{token}")
def guest_landing(request: Request, token: str, db: Annotated[DBSession, Depends(get_db)]):
    config = _get_config_or_404(db, token)
    participant = _get_participant_from_cookie(request, db, config)
    guest_exam_service.record_visit(db, config.id, participant.id if participant else None)
    if participant:
        return RedirectResponse(f"/guest/{token}/exam", status_code=302)

    return templates.TemplateResponse("guest/guest_landing.html", {
        "request": request,
        "config": config,
        "is_open": config.is_active,
        "telegram_login_enabled": telegram_login_enabled(),
    })


@router.get("/guest/{token}/auth/telegram")
@limiter.limit("20/minute")
def guest_telegram_login(request: Request, token: str, db: Annotated[DBSession, Depends(get_db)]):
    """Вход гостя через Telegram — тот же OIDC-флоу, что и у учеников.

    Callback общий (`/auth/telegram-login/callback`, redirect_uri в Telegram
    один), гостевая ветка узнаётся по `purpose` в state — см.
    app/api/auth.py::telegram_oauth_redirect. Проверка членства в закрытом
    канале к гостю не применяется."""
    config = _get_config_or_404(db, token)
    if not config.is_active:
        return templates.TemplateResponse("guest/guest_landing.html", {
            "request": request, "config": config, "is_open": False,
            "telegram_login_enabled": telegram_login_enabled(),
        }, status_code=403)
    if not telegram_login_enabled():
        return templates.TemplateResponse("guest/guest_landing.html", {
            "request": request, "config": config, "is_open": True,
            "telegram_login_enabled": False,
            "error": "Вход через Telegram пока не настроен.",
        }, status_code=503)

    return telegram_oauth_redirect(purpose=TG_PURPOSE_GUEST, guest_token=config.token)


@router.post("/guest/{token}/start")
@limiter.limit("10/minute")
def guest_start(
    request: Request,
    token: str,
    db: Annotated[DBSession, Depends(get_db)],
    display_name: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
):
    """Новый участник или возврат по коду. Без CSRF — до этой точки у гостя ещё
    нет cookie, к которому его можно было бы привязать (тот же принцип, что у
    анонимной формы /login: защита — rate limit, не CSRF)."""
    config = _get_config_or_404(db, token)
    if not config.is_active:
        return templates.TemplateResponse("guest/guest_landing.html", {
            "request": request, "config": config, "is_open": False,
            "telegram_login_enabled": telegram_login_enabled(),
        }, status_code=403)

    error = None
    if code.strip():
        participant = guest_exam_service.get_participant_by_code(db, config.id, code)
        if not participant:
            error = "Код не найден. Проверьте и попробуйте снова."
    elif telegram_login_enabled():
        # Когда вход через Telegram включён, свободный ввод имени на лендинге не
        # показывается — и через прямой POST тоже не работает, иначе личность
        # участника обходилась бы одним запросом мимо Telegram.
        participant = None
        error = "Войдите через Telegram или продолжите по коду участника."
    else:
        try:
            participant = guest_exam_service.create_participant(db, config, display_name)
        except ValueError:
            participant = None
            error = "Введите имя"

    if not participant:
        return templates.TemplateResponse("guest/guest_landing.html", {
            "request": request, "config": config, "is_open": True, "error": error,
            "telegram_login_enabled": telegram_login_enabled(),
        }, status_code=400)

    guest_exam_service.touch_participant(db, participant)
    redirect = RedirectResponse(f"/guest/{token}/exam", status_code=302)
    guest_exam_service.set_guest_cookie(redirect, participant.id, config.token)
    return redirect


@router.get("/guest/{token}/exam")
def guest_exam_page(request: Request, token: str, db: Annotated[DBSession, Depends(get_db)]):
    config = _get_config_or_404(db, token)
    participant = _get_participant_from_cookie(request, db, config)
    if not participant:
        return RedirectResponse(f"/guest/{token}", status_code=302)

    guest_exam_service.touch_participant(db, participant)
    # Билет по второму предмету закрыт, пока по первому не загружена работа.
    pending = guest_exam_service.get_pending_submission(db, participant.id)
    subjects = [
        {
            "name": subject,
            "submission": guest_exam_service.get_submission(db, participant.id, subject),
            "has_tickets": guest_exam_service.has_available_tickets(db, subject),
            "locked_by": pending.subject if pending and pending.subject != subject else None,
        }
        for subject in MOCK_SUBJECTS
    ]

    return templates.TemplateResponse("guest/guest_exam.html", {
        "request": request,
        "config": config,
        "participant": participant,
        "subjects": subjects,
        "is_open": config.is_active,
        "guest_csrf_token": generate_csrf_token(request.cookies.get(GUEST_COOKIE_NAME, "")),
        "visual_duration_minutes": guest_exam_service.VISUAL_DURATION_MINUTES,
    })


@router.post("/guest/{token}/exam/{subject}/ticket")
def guest_issue_ticket(
    request: Request,
    token: str,
    subject: str,
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_guest_csrf)],
):
    config = _get_config_or_404(db, token)
    participant = _get_participant_from_cookie(request, db, config)
    if not participant:
        raise HTTPException(status_code=401)
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=404)
    if not config.is_active:
        raise HTTPException(status_code=403, detail="Приём завершён")

    try:
        guest_exam_service.issue_ticket(db, participant, subject)
    except LookupError:
        raise HTTPException(status_code=409, detail="Билеты по этому предмету пока не готовы")
    except guest_exam_service.TicketLockedError:
        # Реальный сценарий — устаревшая вторая вкладка. Возвращаем на страницу,
        # она перерисуется в актуальном состоянии, без страницы с ошибкой.
        pass

    return RedirectResponse(f"/guest/{token}/exam", status_code=303)


@router.post("/guest/{token}/exam/{subject}/upload")
@limiter.limit("20/minute")
async def guest_upload(
    request: Request,
    token: str,
    subject: str,
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_guest_csrf)],
    photo: Annotated[UploadFile, File()],
):
    config = _get_config_or_404(db, token)
    participant = _get_participant_from_cookie(request, db, config)
    if not participant:
        raise HTTPException(status_code=401)
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=404)
    if not config.is_active:
        raise HTTPException(status_code=403, detail="Приём завершён")

    submission = guest_exam_service.get_submission(db, participant.id, subject)
    if not submission or submission.status != "issued":
        raise HTTPException(status_code=400, detail="Сначала получите билет")

    files_data, error = await read_image_uploads([photo], max_files=1)
    if error:
        raise HTTPException(status_code=400, detail=error)

    filename, raw_bytes = files_data[0]
    compressed = compress_image(raw_bytes)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    s3_path = (
        f"guest-exam/{token}/{participant.participant_code}/{subject}/{uuid.uuid4().hex[:8]}.{ext}"
    )
    s3_url = s3_service.upload_to_s3(
        s3_path, compressed, content_type=photo.content_type or "image/jpeg"
    )

    guest_exam_service.record_upload(db, submission, s3_url, s3_path)

    return RedirectResponse(f"/guest/{token}/exam", status_code=303)
