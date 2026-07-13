import asyncio
import logging
import mimetypes
import random
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session
from app.config import settings
from app.constants import MONTHS, MOCK_SUBJECTS, FEATURE_PORTFOLIO_UPLOAD, FEATURE_MOCK_EXAM, FEATURE_RETAKE
from app.services.exam_cycle import (
    MAX_INTERMEDIATE_PER_FINAL,
    close_or_expire_mock_exam_attempts,
    count_cycle_intermediates,
    cycle_submission_state,
    find_open_cycle_for_ticket,
    intermediate_upload_state,
)
from app.services.feature_periods import is_feature_available
from app.services.mock_exam_access import (
    MOCK_EXAM_DURATION_SEC,
    get_allowed_mock_subjects,
    is_mock_exam_attempt_open,
    is_mock_exam_ticket_start_open,
    is_subject_allowed_for_student,
    mock_exam_deadline_for_started_at,
    mock_exam_window_error,
    ticket_closes_at,
    ticket_duration_sec,
)
from app.csrf import generate_csrf_token
from app.db.database import get_db
from app.dependencies import require_student, require_csrf, get_current_user
from app.models.exam_assignment import ExamTicket
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.mock_exam_lock import MockExamLock
from app.models.upload_log import UploadLog
from app.models.user import User
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.n8n import send_photo_to_n8n
from app.services import s3 as s3_service
from app.services.upload_validation import (
    MAX_UPLOAD_FILE_SIZE,
    MAX_UPLOAD_FILES,
    read_image_uploads,
)
from app.services.utils import compress_image
from app.tmpl import templates, format_ticket_description

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_SIZE = MAX_UPLOAD_FILE_SIZE
MAX_FILES = MAX_UPLOAD_FILES


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


def _serialize_attempt(a: MockExamAttempt, ticket: ExamTicket | None = None) -> dict:
    closes_at = ticket_closes_at(ticket) if ticket is not None else None
    duration_sec = ticket_duration_sec(ticket) if ticket is not None else MOCK_EXAM_DURATION_SEC
    return {
        "id": a.id,
        "subject": a.subject,
        "ticket_id": a.ticket_id,
        "ticket_title": a.ticket_title,
        "ticket_description": format_ticket_description(a.ticket_description),
        "ticket_image_url": a.ticket_image_url or "",
        "started_at": a.started_at.isoformat(),
        "expires_at": mock_exam_deadline_for_started_at(
            a.started_at,
            closes_at=closes_at,
            duration_sec=duration_sec,
        ).isoformat(),
        "duration_sec": duration_sec,
    }


def _locked_mock_subjects(db: DBSession, user_id: int) -> dict[str, str]:
    """Предметы, по которым сдача Пробника закрыта, → причина блокировки.

    Правило «одна сдача на пробник»: финал по любому билету текущего задания
    закрывает предмет, чтобы остальные варианты этого пробника не стали второй
    сдачей. Условие совпадает с бэкенд-гейтом 409 в cycle_upload. Финал «на
    доработку» (needs_revision) за сдачу НЕ считается и оставляет доступным redo.

    Возвращает {subject: reason}:
      waiting — финал сдан, цикл ещё открыт (ждём оценку/ОС).
      scored  — текущий пробник оценён и закрыт, ждём следующий пробник.
    """
    from app.services.exam_cycle import (
        get_active_tickets,
        get_unsubmitted_active_tickets,
        has_submitted_for_ticket,
        has_closed_cycle_for_ticket,
    )

    reasons: dict[str, str] = {}
    for subject in MOCK_SUBJECTS:
        tickets = get_active_tickets(db, user_id, subject)
        if not tickets:
            continue
        if get_unsubmitted_active_tickets(db, user_id, subject):
            continue
        ticket = next(
            (
                t for t in tickets
                if has_submitted_for_ticket(db, user_id, subject, t.id)
            ),
            None,
        )
        if ticket is None:
            continue
        reasons[subject] = (
            "scored" if has_closed_cycle_for_ticket(db, user_id, subject, ticket.id)
            else "waiting"
        )
    return reasons


def _render_mock(request, user, db, *, error=None, success=False, success_count=0, selected_subject="",
                 feature_available=True, feature_message=None):
    now = datetime.now(timezone.utc)
    month_name = MONTHS[now.month - 1].capitalize()
    current_date = f"{now.day} {month_name} {now.year}"
    # Блокировка кнопки предмета — по правилу «одна сдача на пробник»: предмет
    # закрыт после финала по любому билету текущего задания.
    # reason нужен шаблону, чтобы показать верную подсказку (ждёт ОС / оценено).
    locked_reasons = _locked_mock_subjects(db, user["user_id"])
    locked_subjects = set(locked_reasons)

    from app.services.exam_cycle import get_active_tickets, get_unsubmitted_active_tickets

    subjects = get_allowed_mock_subjects(db, user["user_id"])
    active_ticket_lists = {
        subject: get_active_tickets(db, user["user_id"], subject)
        for subject in subjects
    }
    active_tickets = {
        subject: tickets[0]
        for subject, tickets in active_ticket_lists.items()
        if tickets
    }
    unsubmitted_ticket_lists = {
        subject: get_unsubmitted_active_tickets(db, user["user_id"], subject)
        for subject in subjects
    }
    subjects_with_tickets = set(active_tickets)
    subject_start_open = {
        subject: any(is_mock_exam_ticket_start_open(ticket) for ticket in tickets)
        for subject, tickets in unsubmitted_ticket_lists.items()
        if tickets
    }
    subject_window_messages = {
        subject: mock_exam_window_error(
            for_start=not subject_start_open.get(subject, False),
            ticket=tickets[0],
        )
        for subject, tickets in unsubmitted_ticket_lists.items()
        if tickets
    }

    # Активные попытки по subject
    active_attempts = _get_active_attempts(db, user["user_id"])
    attempts_by_subject = {a.subject: _serialize_attempt(a, ticket) for a, ticket in active_attempts}
    stage_upload_state_by_subject: dict[str, dict[str, int]] = {}
    for attempt, ticket in active_attempts:
        cycle = find_open_cycle_for_ticket(
            db,
            user_id=user["user_id"],
            subject=attempt.subject,
            ticket_id=ticket.id if ticket is not None else attempt.ticket_id,
        )
        existing = (
            count_cycle_intermediates(db, cycle_id=cycle.id)
            if cycle is not None
            else 0
        )
        stage_upload_state_by_subject[attempt.subject] = intermediate_upload_state(existing)

    return templates.TemplateResponse("upload_mock.html", {
        "request": request,
        "user": user,
        "max_files": MAX_FILES,
        "max_stage_files": MAX_INTERMEDIATE_PER_FINAL,
        "current_date": current_date,
        "subjects": subjects,
        "selected_subject": selected_subject,
        "locked_subjects": locked_subjects,
        "locked_reasons": locked_reasons,
        "subjects_with_tickets": subjects_with_tickets,
        "error": error,
        "success": success,
        "success_count": success_count,
        "feature_available": feature_available,
        "feature_message": feature_message,
        "attempts_by_subject": attempts_by_subject,
        "stage_upload_state_by_subject": stage_upload_state_by_subject,
        "mock_exam_duration_sec": MOCK_EXAM_DURATION_SEC,
        "subject_start_open": subject_start_open,
        "subject_window_messages": subject_window_messages,
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

    if not settings.n8n_enabled:
        work_ids = [item[0] for item in work_queue]
        if work_ids:
            db = SessionLocal()
            try:
                db.query(Work).filter(Work.id.in_(work_ids)).update(
                    {Work.drive_status: "s3_only"},
                    synchronize_session=False,
                )
                db.commit()
            finally:
                db.close()
        return

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
                    work_id=work_id,
                )
                drive_file_id = result.get("file_id") or result.get("drive_file_id")
                if drive_file_id:
                    _update_work(work_id, drive_file_id=drive_file_id, drive_status="synced")
                    return
                last_error = result.get("error", "n8n returned no file_id/drive_file_id")
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


def _schedule_n8n_mirror_best_effort(
    *,
    background_tasks: BackgroundTasks,
    db: DBSession,
    work_queue: list[tuple[int, str, bytes, str | None]],
    user: dict,
    month: str,
    work_type: str,
) -> None:
    """Schedule the optional Drive mirror without affecting the S3 upload result.

    This function is called only after the S3-backed Work records are committed.
    Even an unexpected failure while registering the background task must not turn
    an already successful student upload into an HTTP 500 or roll it back.
    """
    if not settings.n8n_enabled or not work_queue:
        return

    try:
        background_tasks.add_task(
            _send_to_n8n_background,
            work_queue=work_queue,
            user=user,
            month=month,
            work_type=work_type,
        )
    except Exception:
        work_ids = [item[0] for item in work_queue]
        logger.exception("Could not schedule n8n mirror for work_ids=%s", work_ids)
        try:
            db.query(Work).filter(Work.id.in_(work_ids)).update(
                {Work.drive_status: "failed"},
                synchronize_session=False,
            )
            db.commit()
        except Exception:
            # The primary S3/Work commit already succeeded. Never propagate a
            # secondary status-update error back to the upload response.
            db.rollback()
            logger.exception("Could not mark unscheduled n8n mirror as failed")


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
        s3_configured = s3_service.is_configured()
        if not s3_configured and not settings.n8n_enabled:
            return {"success": False, "error": "Хранилище S3 не настроено. Загрузка временно недоступна."}
        if s3_configured and s3_url is None:
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
            drive_status="pending" if settings.n8n_enabled else "s3_only",
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
        if settings.n8n_enabled:
            n8n_queue.append((work, res["filename"], res["photo_bytes"], res.get("s3_path")))

    if success_count > 0:
        # Source of truth for a successful student upload: S3 + Work/UploadLog.
        # The optional n8n/Drive mirror is scheduled only after this commit.
        db.commit()
        # After commit work.id is available
        if settings.n8n_enabled:
            n8n_queue_with_ids = [(w.id, fn, pb, sp) for w, fn, pb, sp in n8n_queue]
            _schedule_n8n_mirror_best_effort(
                background_tasks=background_tasks,
                db=db,
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
    files_data, err = await _validate_photos(photos)
    if err:
        return _err(err)

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
            if db_user.portfolio_do_completed_at is None:
                db_user.portfolio_do_completed_at = datetime.now(timezone.utc)
            db.commit()
            invalidate_session(user["session_id"])

    return _render_upload(request, user, mode=mode,
                          error=error, success=success_count > 0,
                          success_count=success_count, fail_count=fail_count)


# ── POST /upload/api (JSON) ──────────────────────────────────────────────────

async def _validate_photos(photos: list[UploadFile]) -> tuple[list[tuple[str, bytes]], str | None]:
    """Read & validate uploaded files. Returns (files_data, error_msg or None)."""
    return await read_image_uploads(
        photos,
        max_files=MAX_FILES,
        max_size=MAX_SIZE,
        unsupported_format_error="Файл «{filename}» — неподдерживаемый формат. Допустимы: JPG, PNG, WebP",
    )


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
            if db_user.portfolio_do_completed_at is None:
                db_user.portfolio_do_completed_at = datetime.now(timezone.utc)
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


# ── POST /upload/mock-exam/api ───────────────────────────────────────────────

@router.post("/upload/mock-exam/api")
async def upload_mock_exam_api(
    request: Request,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    subject: str = Form(...),
):
    fa, fm = is_feature_available(db, FEATURE_MOCK_EXAM)
    if not fa:
        return JSONResponse({"success": False, "error": fm or "Пробники закрыты"}, status_code=403)
    if subject not in MOCK_SUBJECTS:
        return JSONResponse({"success": False, "error": "Выберите предмет"}, status_code=422)
    if not is_subject_allowed_for_student(db, user["user_id"], subject):
        return JSONResponse({"success": False, "error": "Этот предмет недоступен для вашей группы"}, status_code=403)

    from app.services.exam_cycle import get_active_tickets

    active_tickets = get_active_tickets(db, user["user_id"], subject)
    if not active_tickets:
        return JSONResponse({"success": False, "error": f"Нет активного билета по предмету «{subject}»"}, status_code=404)
    active_ticket = next(
        (
            ticket for ticket in active_tickets
            if _get_open_attempt_for_ticket(db, user["user_id"], subject, ticket.id)
        ),
        None,
    )
    if not active_ticket:
        return JSONResponse(
            {
                "success": False,
                "error": "Сначала нажмите «Начать пробник». После выдачи билета есть заданное время на сдачу.",
            },
            status_code=403,
        )

    files_data, err = await _validate_photos(photos)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]
    success_count, fail_count, last_error = await _process_uploads(
        background_tasks=background_tasks,
        db=db, user=user, files_data=files_data, month=month, work_type=WORK_TYPE_MOCK_EXAM,
        subject=subject,
        ticket_id=active_ticket.id,
    )

    cycle = find_open_cycle_for_ticket(
        db,
        user_id=user["user_id"],
        subject=subject,
        ticket_id=active_ticket.id,
    )
    submission_state = (
        cycle_submission_state(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM)
        if cycle is not None
        else {"verified": False, "final_work_id": None, "existing": 0, "remaining": MAX_INTERMEDIATE_PER_FINAL, "limit": MAX_INTERMEDIATE_PER_FINAL}
    )

    if success_count > 0 and submission_state["verified"]:
        existing_lock = db.query(MockExamLock).filter(
            MockExamLock.user_id == user["user_id"],
            MockExamLock.subject == subject,
        ).first()
        if existing_lock:
            existing_lock.is_locked = True
            existing_lock.locked_at = datetime.now(timezone.utc)
        else:
            db.add(MockExamLock(
                user_id=user["user_id"],
                subject=subject,
                is_locked=True,
                locked_at=datetime.now(timezone.utc),
        ))
        close_or_expire_mock_exam_attempts(db, user["user_id"], subject, active_ticket.id)
        db.commit()

    api_error = last_error if fail_count and not success_count else None
    if success_count > 0 and not submission_state["verified"]:
        api_error = "Финальное фото не подтверждено в базе. Проверьте соединение и попробуйте отправить ещё раз."

    return JSONResponse({
        "success": success_count > 0 and bool(submission_state["verified"]),
        "created": success_count,
        "failed": fail_count,
        "error": api_error,
        **submission_state,
    })


# ── POST /upload/retake/api ──────────────────────────────────────────────────

@router.post("/upload/retake/api")
async def upload_retake_api(
    request: Request,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    student_score: float = Form(...),
    subject: str = Form(...),
):
    fa, fm = _retake_available_for_user(db, user["user_id"])
    if not fa:
        return JSONResponse({"success": False, "error": fm or "Пересдача закрыта"}, status_code=403)
    if subject not in MOCK_SUBJECTS:
        return JSONResponse({"success": False, "error": "Выберите предмет: Рисунок или Композиция"}, status_code=422)
    if not (0 <= student_score <= 100):
        return JSONResponse({"success": False, "error": "Балл должен быть от 0 до 100"}, status_code=422)
    student_score_int = int(round(student_score))

    files_data, err = await _validate_photos(photos)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]
    success_count, fail_count, last_error = await _process_uploads(
        background_tasks=background_tasks,
        db=db, user=user, files_data=files_data, month=month,
        work_type=WORK_TYPE_RETAKE,
        subject=subject,
        student_score=student_score_int,
    )

    return JSONResponse({
        "success": success_count > 0,
        "created": success_count,
        "failed": fail_count,
        "error": last_error if fail_count and not success_count else None,
    })


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
    if db_user.portfolio_do_completed_at is None:
        db_user.portfolio_do_completed_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_session(user["session_id"])
    return RedirectResponse("/upload", status_code=302)


# ── Mock exam attempt helpers ────────────────────────────────────────────────

def _get_active_attempts(db: DBSession, user_id: int) -> list[tuple[MockExamAttempt, ExamTicket]]:
    """Все активные (незавершённые, неистёкшие) попытки пользователя."""
    attempts = (
        db.query(MockExamAttempt)
        .filter(
            MockExamAttempt.user_id == user_id,
            MockExamAttempt.completed_at.is_(None),
            MockExamAttempt.expired_at.is_(None),
        )
        .all()
    )
    result: list[tuple[MockExamAttempt, ExamTicket]] = []
    for attempt in attempts:
        ticket = _active_ticket_for_attempt(db, attempt.ticket_id, user_id, attempt.subject)
        if ticket and is_mock_exam_attempt_open(
            attempt.started_at,
            closes_at=ticket_closes_at(ticket),
            duration_sec=ticket_duration_sec(ticket),
        ):
            result.append((attempt, ticket))
    return result


def _active_ticket_for_attempt(
    db: DBSession, ticket_id: int | None, user_id: int, subject: str
) -> ExamTicket | None:
    if ticket_id is None:
        return None
    if not is_subject_allowed_for_student(db, user_id, subject):
        return None
    from app.services.exam_cycle import get_active_tickets

    for ticket in get_active_tickets(db, user_id, subject):
        if ticket.id == ticket_id:
            return ticket
    return None


def _get_open_attempt_for_ticket(
    db: DBSession, user_id: int, subject: str, ticket_id: int
) -> MockExamAttempt | None:
    attempt = (
        db.query(MockExamAttempt)
        .filter(
            MockExamAttempt.user_id == user_id,
            MockExamAttempt.subject == subject,
            MockExamAttempt.ticket_id == ticket_id,
            MockExamAttempt.completed_at.is_(None),
            MockExamAttempt.expired_at.is_(None),
        )
        .order_by(MockExamAttempt.started_at.desc())
        .first()
    )
    if not attempt:
        return None
    ticket = _active_ticket_for_attempt(db, ticket_id, user_id, subject)
    if ticket and is_mock_exam_attempt_open(
        attempt.started_at,
        closes_at=ticket_closes_at(ticket),
        duration_sec=ticket_duration_sec(ticket),
    ):
        return attempt
    attempt.expired_at = datetime.now(timezone.utc)
    db.commit()
    return None


def _pick_active_ticket(db: DBSession, user_id: int, subject: str) -> ExamTicket | None:
    if not is_subject_allowed_for_student(db, user_id, subject):
        return None
    from app.services.exam_cycle import get_unsubmitted_active_tickets

    tickets = [
        ticket for ticket in get_unsubmitted_active_tickets(db, user_id, subject)
        if is_mock_exam_ticket_start_open(ticket)
    ]
    if not tickets:
        return None
    if len(tickets) == 1:
        return tickets[0]
    return random.choice(tickets)


def _is_ticket_still_active(db: DBSession, ticket_id: int | None, user_id: int, subject: str) -> bool:
    """True если билет попытки до сих пор входит в набор активных билетов
    предмета для этого ученика (опубликован, в окне дат, назначен).

    Используется, чтобы не резюмировать «зависшую» попытку со снимком билета
    из уже архивного/истёкшего задания.
    """
    return _active_ticket_for_attempt(db, ticket_id, user_id, subject) is not None


# ── GET /upload/mock-exam ────────────────────────────────────────────────────

@router.get("/upload/mock-exam", response_class=HTMLResponse)
def mock_exam_form(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    return _render_mock(request, user, db)


# ── GET /upload/mock-exam/csrf ───────────────────────────────────────────────

@router.get("/upload/mock-exam/csrf")
def mock_exam_csrf(user: Annotated[dict, Depends(get_current_user)]):
    """Свежий CSRF-токен для активной сессии.

    Окно сдачи пробника не ограничено по времени (таймер визуальный), поэтому
    страница может жить дольше, чем срок page-load токена (_MAX_AGE=6ч). Фронт
    дёргает этот эндпоинт перед каждой отправкой и периодически (heartbeat),
    чтобы токен не протух → нет ложного «Неверный CSRF-токен».

    Сам проход через get_current_user продлевает сессию (sliding TTL) и
    переустанавливает cookie session_id — это же лечит «Сессия истекла»/
    «Нет сессии» на долго открытой вкладке.
    """
    return JSONResponse({"csrf_token": generate_csrf_token(user["session_id"])})


# ── POST /upload/mock-exam/start ─────────────────────────────────────────────

@router.post("/upload/mock-exam/start")
def mock_exam_start(
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    subject: str = Form(...),
):
    """Фиксирует начало пробника: выбирает случайный билет и создаёт MockExamAttempt.
    Возвращает JSON с данными билета и started_at для старта клиентского таймера.
    """
    if subject not in MOCK_SUBJECTS:
        return JSONResponse({"error": "invalid_subject"}, status_code=422)
    fa, fm = is_feature_available(db, FEATURE_MOCK_EXAM)
    if not fa:
        return JSONResponse({"error": "feature_closed", "message": fm}, status_code=403)
    if not is_subject_allowed_for_student(db, user["user_id"], subject):
        return JSONResponse({"error": "subject_forbidden"}, status_code=403)
    from app.services.exam_cycle import (
        get_active_tickets,
        get_unsubmitted_active_tickets,
        has_submitted_for_ticket,
    )

    # Финал по любому билету текущего задания закрывает весь пробник. Другой
    # ticket_id этого же задания не является новой попыткой.
    active_tickets = get_active_tickets(db, user["user_id"], subject)
    available_tickets = get_unsubmitted_active_tickets(db, user["user_id"], subject)
    if active_tickets and not available_tickets:
        return JSONResponse({"error": "already_submitted"}, status_code=409)

    existing = (
        db.query(MockExamAttempt)
        .filter(
            MockExamAttempt.user_id == user["user_id"],
            MockExamAttempt.subject == subject,
            MockExamAttempt.completed_at.is_(None),
            MockExamAttempt.expired_at.is_(None),
        )
        .order_by(MockExamAttempt.started_at.desc())
        .first()
    )
    if existing:
        existing_ticket = _active_ticket_for_attempt(
            db, existing.ticket_id, user["user_id"], subject
        )
        if (
            existing_ticket
            and not has_submitted_for_ticket(db, user["user_id"], subject, existing_ticket.id)
            and is_mock_exam_attempt_open(
                existing.started_at,
                closes_at=ticket_closes_at(existing_ticket),
                duration_sec=ticket_duration_sec(existing_ticket),
            )
        ):
            return JSONResponse({
                "attempt_id": existing.id,
                "subject": existing.subject,
                "ticket": {
                    "id": existing.ticket_id,
                    "title": existing.ticket_title,
                    "description": format_ticket_description(existing.ticket_description),
                    "image_url": existing.ticket_image_url or "",
                },
                "started_at": existing.started_at.isoformat(),
                "expires_at": mock_exam_deadline_for_started_at(
                    existing.started_at,
                    closes_at=ticket_closes_at(existing_ticket),
                    duration_sec=ticket_duration_sec(existing_ticket),
                ).isoformat(),
                "duration_sec": ticket_duration_sec(existing_ticket),
                "resumed": True,
            })
        # Билет этой попытки больше не активен (период истёк / задание архивно) —
        # попытка протухла сама, без сдачи. Помечаем отдельно от completed_at,
        # чтобы не путать со «сдано», и начинаем новую попытку с актуальным билетом.
        existing.expired_at = datetime.now(timezone.utc)
        db.commit()

    if not active_tickets:
        return JSONResponse({"error": "no_active_ticket"}, status_code=404)
    if not any(is_mock_exam_ticket_start_open(ticket) for ticket in available_tickets):
        return JSONResponse(
            {
                "error": "start_window_closed",
                "message": mock_exam_window_error(for_start=True, ticket=available_tickets[0]),
            },
            status_code=403,
        )

    ticket = _pick_active_ticket(db, user["user_id"], subject)
    if not ticket:
        return JSONResponse({"error": "no_active_ticket"}, status_code=404)

    attempt = MockExamAttempt(
        user_id=user["user_id"],
        subject=subject,
        ticket_id=ticket.id,
        ticket_title=ticket.title,
        ticket_description=ticket.description,
        ticket_image_url=ticket.image_s3_url,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return JSONResponse({
        "attempt_id": attempt.id,
        "subject": subject,
        "ticket": {
            "id": ticket.id,
            "title": ticket.title,
            "description": format_ticket_description(ticket.description),
            "image_url": ticket.image_s3_url or "",
        },
        "started_at": attempt.started_at.isoformat(),
        "expires_at": mock_exam_deadline_for_started_at(
            attempt.started_at,
            closes_at=ticket_closes_at(ticket),
            duration_sec=ticket_duration_sec(ticket),
        ).isoformat(),
        "duration_sec": ticket_duration_sec(ticket),
        "resumed": False,
    })


# ── POST /upload/mock-exam ───────────────────────────────────────────────────

@router.post("/upload/mock-exam", response_class=HTMLResponse)
async def upload_mock_exam(
    request: Request,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    subject: str = Form(...),
):
    fa, fm = is_feature_available(db, FEATURE_MOCK_EXAM)
    if not fa:
        return _render_mock(request, user, db, feature_available=fa, feature_message=fm)

    def _err(msg):
        return _render_mock(request, user, db, error=msg, selected_subject=subject)

    if subject not in MOCK_SUBJECTS:
        return _err("Выберите предмет")
    if not is_subject_allowed_for_student(db, user["user_id"], subject):
        return _err("Этот предмет недоступен для вашей группы")

    now = datetime.now(timezone.utc)
    from app.services.exam_cycle import get_active_tickets, get_unsubmitted_active_tickets

    active_tickets = get_active_tickets(db, user["user_id"], subject)
    if not active_tickets:
        return _err(f"Нет активного билета по предмету «{subject}»")
    available_tickets = get_unsubmitted_active_tickets(db, user["user_id"], subject)
    if not available_tickets:
        return _err(
            f"Пробник по «{subject}» уже сдан в текущем цикле. Дождись обратной связи куратора."
        )
    active_ticket = next(
        (
            ticket for ticket in available_tickets
            if _get_open_attempt_for_ticket(db, user["user_id"], subject, ticket.id)
        ),
        None,
    )
    if not active_ticket:
        return _err("Сначала нажмите «Начать пробник». После выдачи билета есть заданное время на сдачу.")
    month = MONTHS[now.month - 1]

    files_data, err = await _validate_photos(photos)
    if err:
        return _err(err)

    success_count, fail_count, last_error = await _process_uploads(
        background_tasks=background_tasks,
        db=db, user=user, files_data=files_data, month=month, work_type=WORK_TYPE_MOCK_EXAM,
        subject=subject,
        ticket_id=active_ticket.id,
    )

    error = None
    if fail_count > 0 and success_count == 0:
        error = f"Не удалось загрузить: {last_error}"
    elif fail_count > 0:
        error = f"{fail_count} фото не загружено"

    cycle = find_open_cycle_for_ticket(
        db,
        user_id=user["user_id"],
        subject=subject,
        ticket_id=active_ticket.id,
    )
    submission_state = (
        cycle_submission_state(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM)
        if cycle is not None
        else {"verified": False, "final_work_id": None, "existing": 0, "remaining": MAX_INTERMEDIATE_PER_FINAL, "limit": MAX_INTERMEDIATE_PER_FINAL}
    )

    if success_count > 0 and submission_state["verified"]:
        existing_lock = db.query(MockExamLock).filter(
            MockExamLock.user_id == user["user_id"],
            MockExamLock.subject == subject,
        ).first()
        if existing_lock:
            existing_lock.is_locked = True
            existing_lock.locked_at = datetime.now(timezone.utc)
        else:
            db.add(MockExamLock(
                user_id=user["user_id"],
                subject=subject,
                is_locked=True,
                locked_at=datetime.now(timezone.utc),
            ))
        close_or_expire_mock_exam_attempts(db, user["user_id"], subject, active_ticket.id)
        db.commit()

    if success_count > 0 and not submission_state["verified"] and error is None:
        error = "Финальное фото не подтверждено в базе. Проверьте соединение и попробуйте отправить ещё раз."

    return _render_mock(request, user, db, error=error,
                        success=success_count > 0 and bool(submission_state["verified"]),
                        success_count=success_count,
                        selected_subject="" if success_count > 0 and submission_state["verified"] else subject)


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

@router.post("/upload/retake", response_class=HTMLResponse)
async def upload_retake(
    request: Request,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    student_score: float = Form(...),
    subject: str = Form(...),
):
    fa, fm = _retake_available_for_user(db, user["user_id"])
    if not fa:
        return _render_retake(request, user, feature_available=fa, feature_message=fm)

    def _err(msg):
        return _render_retake(request, user, error=msg,
                              student_score_input=str(student_score) if student_score is not None else "",
                              selected_subject=subject or "")

    if subject not in MOCK_SUBJECTS:
        return _err("Выберите предмет: Рисунок или Композиция")
    if not (0 <= student_score <= 100):
        return _err("Балл должен быть от 0 до 100")
    student_score = int(round(student_score))

    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]

    files_data, err = await _validate_photos(photos)
    if err:
        return _err(err)

    success_count, fail_count, last_error = await _process_uploads(
        background_tasks=background_tasks,
        db=db, user=user, files_data=files_data, month=month,
        work_type=WORK_TYPE_RETAKE,
        subject=subject,
        student_score=student_score,
    )

    error = None
    if fail_count > 0 and success_count == 0:
        error = f"Не удалось загрузить: {last_error}"
    elif fail_count > 0:
        error = f"{fail_count} фото не загружено"

    return _render_retake(request, user, error=error,
                          success=success_count > 0, success_count=success_count,
                          student_score_input="" if success_count > 0 else str(student_score),
                          selected_subject="" if success_count > 0 else subject)
