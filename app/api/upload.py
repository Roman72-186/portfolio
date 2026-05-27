import asyncio
import logging
import mimetypes
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session
from app.constants import MONTHS, MOCK_SUBJECTS, FEATURE_PORTFOLIO_UPLOAD, FEATURE_MOCK_EXAM, FEATURE_RETAKE
from app.services.feature_periods import get_active_period, is_feature_available
from app.services.tz import msk_midnight, today_msk
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.mock_exam_lock import MockExamLock
from app.models.upload_log import UploadLog
from app.models.user import User
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.n8n import send_photo_to_n8n
from app.services import s3 as s3_service
from app.services.utils import compress_image
from app.tmpl import templates, format_ticket_description

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_SIZE = 10 * 1024 * 1024  # 10 MB per file
MAX_FILES = 10


_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".jfif",
    ".png", ".apng",
    ".webp",
    ".heic", ".heif", ".avif",
    ".gif", ".bmp", ".tif", ".tiff",
    ".svg",
}


def _is_allowed_image(content_type: str | None, filename: str | None) -> bool:
    """Broad image acceptance: любой image/*, octet-stream/пустой MIME с
    image-расширением, либо неизвестный MIME с image-расширением.
    Отклоняем явные не-картинки (application/pdf, video/*, audio/*, text/*)."""
    ct = (content_type or "").lower()
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""

    if ct.startswith("image/"):
        return True
    if ct.startswith(("video/", "audio/", "text/", "application/pdf", "application/zip")):
        return False
    # octet-stream / binary / пустой / unknown application → принимаем если расширение image-like
    if ext in _IMAGE_EXTENSIONS:
        return True
    # Mobile quirk: некоторые браузеры шлют octet-stream — принимаем только image-расширения
    if ct in ("application/octet-stream", "binary/octet-stream", "") and ext in _IMAGE_EXTENSIONS:
        return True
    return False


def _now_year() -> int:
    return datetime.now(timezone.utc).year


def _default_month() -> str:
    return MONTHS[datetime.now(timezone.utc).month - 1]


def _resolve_upload_mode(user: dict, requested_section: str | None) -> str:
    section = (requested_section or "").strip().lower()
    if section in {"before", "after"}:
        return section
    return "before" if not user.get("portfolio_do_completed") else "after"


def _render_upload(request, user, *, mode: str = "after", error=None, success=False,
                   success_count=0, fail_count=0, feature_available=True, feature_message=None):
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "user": user,
        "months": MONTHS,
        "max_files": MAX_FILES,
        "mode": mode,           # "before" | "after"
        "error": error,
        "success": success,
        "success_count": success_count,
        "fail_count": fail_count,
        "feature_available": feature_available,
        "feature_message": feature_message,
    })


def _serialize_attempt(a: MockExamAttempt) -> dict:
    return {
        "id": a.id,
        "subject": a.subject,
        "ticket_id": a.ticket_id,
        "ticket_title": a.ticket_title,
        "ticket_description": format_ticket_description(a.ticket_description),
        "ticket_image_url": a.ticket_image_url or "",
        "started_at": a.started_at.isoformat(),
    }


def _submitted_mock_subjects_in_active_period(db: DBSession, user_id: int) -> set[str]:
    period = get_active_period(db, FEATURE_MOCK_EXAM)
    if not period:
        return set()
    period_start = msk_midnight(period.start_date)
    period_end = msk_midnight(period.end_date + timedelta(days=1))
    rows = (
        db.query(Work.subject)
        .filter(
            Work.user_id == user_id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.status == "success",
            Work.score.is_(None),
            Work.created_at >= period_start,
            Work.created_at < period_end,
            Work.subject.in_(MOCK_SUBJECTS),
        )
        .distinct()
        .all()
    )
    submitted_subjects = {row[0] for row in rows if row[0]}
    if not submitted_subjects:
        return set()

    locks = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == user_id,
            MockExamLock.subject.in_(submitted_subjects),
        )
        .all()
    )
    lock_by_subject = {lock.subject: lock for lock in locks}
    return {
        subject
        for subject in submitted_subjects
        if not (subject in lock_by_subject and lock_by_subject[subject].is_locked is False)
    }


def _render_mock(request, user, db, *, error=None, success=False, success_count=0, selected_subject="",
                 feature_available=True, feature_message=None):
    now = datetime.now(timezone.utc)
    month_name = MONTHS[now.month - 1].capitalize()
    current_date = f"{now.day} {month_name} {now.year}"
    # Old MockExamLock rows are informational for curator/admin review queues.
    # The student UI should only lock subjects submitted in the current active period.
    locked_subjects = _submitted_mock_subjects_in_active_period(db, user["user_id"])

    today = today_msk()
    ticket_rows = (
        db.query(ExamAssignment.subject)
        .join(ExamTicket, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(
            ExamAssignment.status == "published",
            ExamAssignment.subject.in_(MOCK_SUBJECTS),
            ExamTicket.start_date <= today,
            ExamTicket.end_date >= today,
            or_(
                ExamTicket.assign_to_all.is_(True),
                ExamTicket.id.in_(
                    db.query(ExamTicketAssignee.ticket_id)
                    .filter(ExamTicketAssignee.user_id == user["user_id"])
                    .scalar_subquery()
                ),
            ),
        )
        .distinct()
        .all()
    )
    subjects_with_tickets = {row[0] for row in ticket_rows}

    # Активные попытки по subject
    active_attempts = _get_active_attempts(db, user["user_id"])
    attempts_by_subject = {a.subject: _serialize_attempt(a) for a in active_attempts}

    return templates.TemplateResponse("upload_mock.html", {
        "request": request,
        "user": user,
        "max_files": MAX_FILES,
        "current_date": current_date,
        "subjects": MOCK_SUBJECTS,
        "selected_subject": selected_subject,
        "locked_subjects": locked_subjects,
        "subjects_with_tickets": subjects_with_tickets,
        "error": error,
        "success": success,
        "success_count": success_count,
        "feature_available": feature_available,
        "feature_message": feature_message,
        "attempts_by_subject": attempts_by_subject,
        "mock_exam_duration_sec": MOCK_EXAM_DURATION_SEC,
    })


def _render_retake(request, user, *, error=None, success=False, success_count=0,
                   student_score_input: str = "", selected_subject: str = "",
                   feature_available=True, feature_message=None):
    now = datetime.now(timezone.utc)
    month_name = MONTHS[now.month - 1].capitalize()
    current_date = f"{now.day} {month_name} {now.year}"
    return templates.TemplateResponse("upload_retake.html", {
        "request": request,
        "user": user,
        "max_files": MAX_FILES,
        "current_date": current_date,
        "student_score_input": student_score_input,
        "selected_subject": selected_subject,
        "subjects": MOCK_SUBJECTS,
        "error": error,
        "success": success,
        "success_count": success_count,
        "feature_available": feature_available,
        "feature_message": feature_message,
    })


def _has_retake_assignment(db: DBSession, user_id: int) -> bool:
    return db.query(Work.id).filter(
        Work.user_id == user_id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.status == "success",
        Work.sent_to_retake == True,  # noqa: E712
    ).first() is not None


def _retake_available_for_user(db: DBSession, user_id: int) -> tuple[bool, str | None]:
    fa, fm = is_feature_available(db, FEATURE_RETAKE)
    if fa or _has_retake_assignment(db, user_id):
        return True, None
    return fa, fm


_N8N_MAX_RETRIES = 3
_N8N_RETRY_DELAYS = [5, 15]  # seconds between attempt 1→2 and 2→3


async def _send_to_n8n_background(
    work_queue: list[tuple[int, str, bytes, str | None]],
    user: dict,
    month: str,
    work_type: str,
) -> None:
    """Background task: send photos to n8n, update drive_file_id / drive_status when done.
    work_queue: list of (work_id, filename, photo_bytes, s3_path)
    First photo is sent sequentially (creates Drive folder), rest in parallel.
    """
    from app.db.database import SessionLocal

    def _update_work(work_id: int, *, drive_file_id: str | None = None, drive_status: str) -> None:
        db = SessionLocal()
        try:
            work = db.query(Work).filter(Work.id == work_id).first()
            if work:
                work.drive_status = drive_status
                if drive_file_id:
                    work.drive_file_id = drive_file_id
                db.commit()
        finally:
            db.close()

    async def _send_one(work_id: int, filename: str, photo_bytes: bytes, s3_path: str | None) -> None:
        last_error: str | Exception = "unknown error"
        for attempt in range(_N8N_MAX_RETRIES):
            if attempt > 0:
                delay = _N8N_RETRY_DELAYS[min(attempt - 1, len(_N8N_RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)
            try:
                result = await send_photo_to_n8n(
                    user_id=user["vk_id"],
                    student_name=user["name"],
                    tariff=user["tariff"],
                    month=month,
                    photo_bytes=photo_bytes,
                    filename=filename,
                    photo_type=work_type,
                    s3_path=s3_path,
                )
                if result.get("file_id"):
                    _update_work(work_id, drive_file_id=result["file_id"], drive_status="synced")
                    return
                last_error = result.get("error", "n8n returned no file_id")
            except Exception as exc:
                last_error = exc
        # All retries exhausted
        logger.error("n8n upload failed after %s retries for work_id=%s: %s",
                     _N8N_MAX_RETRIES, work_id, last_error)
        _update_work(work_id, drive_status="failed")

    if not work_queue:
        return

    # First photo sequential (n8n creates Drive folder on first upload)
    try:
        await _send_one(*work_queue[0])
    except Exception as exc:
        logger.error("n8n background send_one raised for work_id=%s: %s", work_queue[0][0], exc)

    # Remaining in parallel
    if len(work_queue) > 1:
        results = await asyncio.gather(
            *[_send_one(*item) for item in work_queue[1:]],
            return_exceptions=True,
        )
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                work_id = work_queue[i + 1][0]
                logger.error("n8n background send_one raised for work_id=%s: %s", work_id, res)


async def _process_uploads(
    *,
    background_tasks: BackgroundTasks,
    db: DBSession,
    user: dict,
    files_data: list[tuple[str, bytes]],
    month: str,
    work_type: str,
    subject: str | None = None,
    student_score: float | None = None,
    ticket_id: int | None = None,
) -> tuple[int, int, str]:
    """Upload files to S3, create Work records immediately, send to n8n in background.
    Returns (success, fail, last_error).

    Для mock_exam и retake — автоматическое создание/привязка ExamCycle:
    весь upload-сеанс = одна «финальная попытка» (is_final=True, общий attempt_number).
    """
    success_count = 0
    fail_count = 0
    last_error = ""
    year = _now_year()
    vk_id = user["vk_id"]
    tariff = user["tariff"]

    # ── Цикл Пробника: получить или создать ──
    cycle_id: int | None = None
    attempt_no: int | None = None
    if work_type in (WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE) and subject:
        from app.services import exam_cycle as cycle_service
        if work_type == WORK_TYPE_MOCK_EXAM:
            cycle, _created = cycle_service.get_or_create_cycle_for_probnik(
                db, user_id=user["user_id"], subject=subject, ticket_id=ticket_id,
            )
        else:
            cycle = cycle_service.find_latest_cycle(db, user["user_id"], subject)
            if cycle is None:
                cycle, _created = cycle_service.get_or_create_cycle_for_probnik(
                    db, user_id=user["user_id"], subject=subject, ticket_id=None,
                )
        cycle_id = cycle.id
        attempt_no = cycle_service.next_attempt_number(
            db, cycle_id=cycle_id, work_type=work_type,
        )

    def _build_s3_path(filename: str) -> str:
        if work_type == WORK_TYPE_BEFORE:
            return s3_service.s3_path_before(vk_id, tariff, filename)
        if work_type == WORK_TYPE_MOCK_EXAM:
            return s3_service.s3_path_mock_exam(vk_id, tariff, filename)
        if work_type == WORK_TYPE_RETAKE:
            return s3_service.s3_path_retake(vk_id, tariff, filename)
        return s3_service.s3_path_after(vk_id, tariff, filename)

    async def _upload_to_s3(filename: str, photo_bytes: bytes) -> dict:
        """Compress, upload to S3 — fast path shown to the user."""
        s3_path = _build_s3_path(filename)
        loop = asyncio.get_running_loop()

        def _compress_and_upload():
            compressed = compress_image(photo_bytes)
            url = s3_service.upload_to_s3(s3_path, compressed, "image/jpeg")
            return compressed, url

        compressed_bytes, s3_url = await loop.run_in_executor(None, _compress_and_upload)
        if s3_service.is_configured() and s3_url is None:
            return {"success": False, "error": "Ошибка загрузки в хранилище. Попробуйте ещё раз."}
        # Use compressed bytes for n8n as well — smaller base64 payload
        return {"success": True, "filename": filename, "photo_bytes": compressed_bytes,
                "s3_url": s3_url, "s3_path": s3_path}

    # Upload ALL photos to S3 in parallel
    s3_results = await asyncio.gather(
        *[_upload_to_s3(fn, b) for fn, b in files_data],
        return_exceptions=True,
    )

    # Create Work + UploadLog records for successful S3 uploads
    n8n_queue: list[tuple[int, str, bytes, str | None]] = []

    for res in s3_results:
        if isinstance(res, Exception):
            fail_count += 1
            last_error = str(res)
            continue
        if not res.get("success"):
            fail_count += 1
            last_error = res.get("error", "")
            continue

        work = Work(
            user_id=user["user_id"],
            work_type=work_type,
            month=month,
            year=year,
            filename=res["filename"],
            s3_url=res.get("s3_url"),
            s3_path=res.get("s3_path"),
            subject=subject,
            tariff=user["tariff"],
            student_score=student_score,
            status="success",
            cycle_id=cycle_id,
            is_final=True if cycle_id else None,
            attempt_number=attempt_no,
        )
        db.add(work)

        log = UploadLog(
            user_id=user["user_id"],
            student_name=user["name"],
            tariff=user["tariff"],
            month=month,
            photo_type=work_type,
            photo_count=1,
            status="success",
        )
        db.add(log)
        success_count += 1
        n8n_queue.append((work, res["filename"], res["photo_bytes"], res.get("s3_path")))

    if success_count > 0:
        db.commit()
        # After commit work.id is available
        n8n_queue_with_ids = [(w.id, fn, pb, sp) for w, fn, pb, sp in n8n_queue]
        background_tasks.add_task(
            _send_to_n8n_background,
            work_queue=n8n_queue_with_ids,
            user=user,
            month=month,
            work_type=work_type,
        )

    return success_count, fail_count, last_error


# ── GET /upload ─────────────────────────────────────────────────────────────

@router.get("/upload", response_class=HTMLResponse)
def upload_form(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    section: str | None = None,
):
    mode = _resolve_upload_mode(user, section)
    if mode == "after":
        fa, fm = is_feature_available(db, FEATURE_PORTFOLIO_UPLOAD)
    else:
        fa, fm = True, None
    return _render_upload(request, user, mode=mode, feature_available=fa, feature_message=fm)


# ── POST /upload ─────────────────────────────────────────────────────────────

@router.post("/upload", response_class=HTMLResponse)
async def upload_photos(
    request: Request,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    month: str | None = Form(default=None),
    section: str | None = Form(default=None),
):
    mode = _resolve_upload_mode(user, section)
    work_type = WORK_TYPE_BEFORE if mode == "before" else WORK_TYPE_AFTER

    if mode == "after":
        fa, fm = is_feature_available(db, FEATURE_PORTFOLIO_UPLOAD)
        if not fa:
            return _render_upload(request, user, mode=mode, feature_available=fa, feature_message=fm)

    def _err(msg):
        return _render_upload(request, user, mode=mode, error=msg)

    if mode == "before":
        month = _default_month()
    elif month not in MONTHS:
        return _err("Выберите месяц")
    if not photos or (len(photos) == 1 and not photos[0].filename):
        return _err("Выберите хотя бы одно фото")
    if len(photos) > MAX_FILES:
        return _err(f"Максимум {MAX_FILES} фото за раз")

    files_data = []
    for photo in photos:
        if not _is_allowed_image(photo.content_type, photo.filename):
            return _err(f"Файл «{photo.filename}» — неподдерживаемый формат. Допустимы: JPG, PNG, WebP")
        photo_bytes = await photo.read()
        if len(photo_bytes) > MAX_SIZE:
            return _err(f"Файл «{photo.filename}» слишком большой (макс. 10 МБ)")
        files_data.append((photo.filename or "photo.jpg", photo_bytes))

    success_count, fail_count, last_error = await _process_uploads(
        background_tasks=background_tasks,
        db=db, user=user, files_data=files_data, month=month, work_type=work_type,
    )

    error = None
    if fail_count > 0 and success_count == 0:
        error = f"Не удалось загрузить: {last_error}"
    elif fail_count > 0:
        error = f"{fail_count} фото не загружено"

    # Auto-complete portfolio onboarding on first successful BEFORE upload
    if success_count > 0 and work_type == WORK_TYPE_BEFORE and not user.get("portfolio_do_completed"):
        db_user = db.query(User).filter(User.id == user["user_id"]).first()
        if db_user:
            db_user.portfolio_do_completed = True
            db.commit()
            invalidate_session(user["session_id"])

    return _render_upload(request, user, mode=mode,
                          error=error, success=success_count > 0,
                          success_count=success_count, fail_count=fail_count)


# ── POST /upload/api (JSON) ──────────────────────────────────────────────────

async def _validate_photos(photos: list[UploadFile]) -> tuple[list[tuple[str, bytes]], str | None]:
    """Read & validate uploaded files. Returns (files_data, error_msg or None)."""
    if not photos or (len(photos) == 1 and not photos[0].filename):
        return [], "Выберите хотя бы одно фото"
    if len(photos) > MAX_FILES:
        return [], f"Максимум {MAX_FILES} фото за раз"
    files_data: list[tuple[str, bytes]] = []
    for photo in photos:
        if not _is_allowed_image(photo.content_type, photo.filename):
            return [], f"Файл «{photo.filename}» — неподдерживаемый формат. Допустимы: JPG, PNG, WebP"
        photo_bytes = await photo.read()
        if len(photo_bytes) > MAX_SIZE:
            return [], f"Файл «{photo.filename}» слишком большой (макс. 10 МБ)"
        files_data.append((photo.filename or "photo.jpg", photo_bytes))
    return files_data, None


@router.post("/upload/api")
async def upload_photos_api(
    request: Request,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    month: str | None = Form(default=None),
    section: str | None = Form(default=None),
):
    """AJAX-friendly вариант POST /upload — возвращает JSON вместо редиректа."""
    mode = _resolve_upload_mode(user, section)
    work_type = WORK_TYPE_BEFORE if mode == "before" else WORK_TYPE_AFTER

    if mode == "after":
        fa, fm = is_feature_available(db, FEATURE_PORTFOLIO_UPLOAD)
        if not fa:
            return JSONResponse({"success": False, "error": fm or "Раздел закрыт"}, status_code=403)

    if mode == "before":
        month = _default_month()
    elif month not in MONTHS:
        return JSONResponse({"success": False, "error": "Выберите месяц"}, status_code=422)

    files_data, err = await _validate_photos(photos)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    success_count, fail_count, last_error = await _process_uploads(
        background_tasks=background_tasks,
        db=db, user=user, files_data=files_data, month=month, work_type=work_type,
    )

    mode_changed = False
    if success_count > 0 and work_type == WORK_TYPE_BEFORE and not user.get("portfolio_do_completed"):
        db_user = db.query(User).filter(User.id == user["user_id"]).first()
        if db_user:
            db_user.portfolio_do_completed = True
            db.commit()
            invalidate_session(user["session_id"])
            mode_changed = True

    return JSONResponse({
        "success": success_count > 0,
        "created": success_count,
        "failed": fail_count,
        "error": last_error if fail_count and not success_count else None,
        "mode_changed": mode_changed,
    })


# ARCHIVED 2026-05-21: оригинальные тела /upload/mock-exam/api и /upload/retake/api
# вынесены в _cleanup/legacy-upload-routes-2026-05-21.py. Сейчас stubs возвращают
# 410 Gone, чтобы старые AJAX-клиенты/закладки не могли обойти MockExamLock
# pre-check, который живёт в /upload/probnik/final и /upload/otrabotka/final.
_LEGACY_GONE_MSG = "Этот endpoint устарел. Загрузка пробников и отработок идёт через /cabinet/cycle."


@router.post("/upload/mock-exam/api")
async def upload_mock_exam_api_legacy():
    return JSONResponse({"success": False, "error": _LEGACY_GONE_MSG}, status_code=410)


@router.post("/upload/retake/api")
async def upload_retake_api_legacy():
    return JSONResponse({"success": False, "error": _LEGACY_GONE_MSG}, status_code=410)


# ── POST /upload/finish-before ───────────────────────────────────────────────

@router.post("/upload/finish-before", response_class=HTMLResponse)
async def finish_before(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Mark BEFORE section as completed. Requires at least 1 successful BEFORE upload."""
    if user.get("portfolio_do_completed"):
        return RedirectResponse("/upload", status_code=302)

    has_before = db.query(Work).filter(
        Work.user_id == user["user_id"],
        Work.work_type == WORK_TYPE_BEFORE,
        Work.status == "success",
    ).first()

    if not has_before:
        return _render_upload(
            request, user, mode="before",
            error="Загрузите хотя бы одно фото «До» перед завершением",
        )

    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    db_user.portfolio_do_completed = True
    db.commit()
    invalidate_session(user["session_id"])
    return RedirectResponse("/upload", status_code=302)


# ── Mock exam attempt helpers ────────────────────────────────────────────────

import random as _random

MOCK_EXAM_DURATION_SEC = 4 * 3600  # 4 часа


def _get_active_attempts(db: DBSession, user_id: int) -> list[MockExamAttempt]:
    """Все активные (незавершённые) попытки пользователя."""
    return (
        db.query(MockExamAttempt)
        .filter(
            MockExamAttempt.user_id == user_id,
            MockExamAttempt.completed_at.is_(None),
        )
        .all()
    )


def _pick_random_active_ticket(db: DBSession, user_id: int, subject: str) -> ExamTicket | None:
    today = today_msk()
    tickets = (
        db.query(ExamTicket)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(
            ExamAssignment.status == "published",
            ExamAssignment.subject == subject,
            ExamTicket.start_date <= today,
            ExamTicket.end_date >= today,
            or_(
                ExamTicket.assign_to_all.is_(True),
                ExamTicket.id.in_(
                    db.query(ExamTicketAssignee.ticket_id)
                    .filter(ExamTicketAssignee.user_id == user_id)
                    .scalar_subquery()
                ),
            ),
        )
        .all()
    )
    return _random.choice(tickets) if tickets else None


# ── GET /upload/mock-exam ────────────────────────────────────────────────────

@router.get("/upload/mock-exam", response_class=HTMLResponse)
def mock_exam_form(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    fa, fm = is_feature_available(db, FEATURE_MOCK_EXAM)
    return _render_mock(request, user, db, feature_available=fa, feature_message=fm)


# ── POST /upload/mock-exam/start ─────────────────────────────────────────────
# ARCHIVED 2026-05-21: см. _cleanup/legacy-upload-routes-2026-05-21.py.
# Создание MockExamAttempt больше не нужно — новый cycle flow начинается с
# /upload/probnik/final, билеты в нём показываются без отдельного start-вызова.

@router.post("/upload/mock-exam/start")
def mock_exam_start_legacy():
    return JSONResponse({"error": "gone", "message": _LEGACY_GONE_MSG}, status_code=410)


# ── POST /upload/mock-exam ───────────────────────────────────────────────────
# ARCHIVED 2026-05-21: form POST на /upload/mock-exam не проверял MockExamLock
# pre-check и мог обойти блокировку повторной сдачи. Заменён на
# /upload/probnik/final с pre-check в app/api/cycle_upload.py.

@router.post("/upload/mock-exam", response_class=HTMLResponse)
async def upload_mock_exam_legacy():
    return JSONResponse({"success": False, "error": _LEGACY_GONE_MSG}, status_code=410)


# ── GET /upload/retake ───────────────────────────────────────────────────────

@router.get("/upload/retake", response_class=HTMLResponse)
def retake_form(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    fa, fm = _retake_available_for_user(db, user["user_id"])
    return _render_retake(request, user, feature_available=fa, feature_message=fm)


# ── POST /upload/retake ──────────────────────────────────────────────────────
# ARCHIVED 2026-05-21: см. _cleanup/legacy-upload-routes-2026-05-21.py.
# Заменён на /upload/otrabotka/final в app/api/cycle_upload.py.

@router.post("/upload/retake", response_class=HTMLResponse)
async def upload_retake_legacy():
    return JSONResponse({"success": False, "error": _LEGACY_GONE_MSG}, status_code=410)
