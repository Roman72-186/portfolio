"""Роуты цикла Пробника (план 2026-05-14).

Эндпоинты:
  POST /upload/probnik/final         — финальная Пробника (создаёт/обновляет цикл)
  POST /upload/probnik/intermediate  — промежуточная (до 10 на финальную)
  POST /upload/otrabotka/final       — финальная Отработки (требует существующий цикл)
  POST /upload/otrabotka/intermediate — промежуточная

Новый флоу: только S3, без n8n/Google Drive (drive_status='s3_only').
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.constants import MONTHS, MOCK_SUBJECTS, FEATURE_MOCK_EXAM, FEATURE_RETAKE
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.mock_exam_lock import MockExamLock
from app.models.upload_log import UploadLog
from app.models.work import (
    Work,
    WORK_TYPE_MOCK_EXAM,
    WORK_TYPE_RETAKE,
)
from app.services import s3 as s3_service
from app.services.exam_cycle import (
    find_latest_cycle,
    get_or_create_cycle_for_probnik,
    next_attempt_number,
)
from app.services.feature_periods import is_feature_available
from app.services.tz import today_msk
from app.services.utils import compress_image

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_SIZE = 10 * 1024 * 1024
MAX_FILES = 10
MAX_INTERMEDIATE_PER_FINAL = 10

_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".webp",
    ".heic", ".heif", ".avif", ".gif", ".bmp", ".tif", ".tiff", ".svg",
}


def _is_allowed_image(content_type: str | None, filename: str | None) -> bool:
    ct = (content_type or "").lower()
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""
    if ct.startswith("image/"):
        return True
    if ct.startswith(("video/", "audio/", "text/", "application/pdf", "application/zip")):
        return False
    return ext in _IMAGE_EXTENSIONS


async def _read_photos(photos: list[UploadFile], *, max_files: int) -> tuple[list[tuple[str, bytes]], str | None]:
    if not photos or (len(photos) == 1 and not photos[0].filename):
        return [], "Выберите хотя бы одно фото"
    if len(photos) > max_files:
        return [], f"Максимум {max_files} фото за раз"
    out: list[tuple[str, bytes]] = []
    for p in photos:
        if not _is_allowed_image(p.content_type, p.filename):
            return [], f"Файл «{p.filename}» — неподдерживаемый формат"
        data = await p.read()
        if len(data) > MAX_SIZE:
            return [], f"Файл «{p.filename}» слишком большой (макс. 10 МБ)"
        out.append((p.filename or "photo.jpg", data))
    return out, None


def _has_active_ticket(db: DBSession, user_id: int, subject: str) -> ExamTicket | None:
    today = today_msk()
    return (
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
        .first()
    )


async def _upload_and_save(
    *,
    db: DBSession,
    user: dict,
    files: list[tuple[str, bytes]],
    work_type: str,
    cycle_id: int,
    attempt_number: int | None,
    is_final: bool,
    parent_work_id: int | None,
    subject: str | None,
    student_score: float | None,
    s3_path_builder,
    month: str,
    year: int,
) -> tuple[int, int, str, list[int]]:
    """Сжать → S3 → Work. Возвращает (success, fail, last_error, created_ids)."""
    loop = asyncio.get_running_loop()
    success = 0
    fail = 0
    last_error = ""
    created_ids: list[int] = []

    async def _upload_one(fn: str, data: bytes):
        path = s3_path_builder(fn)

        def _do():
            compressed = compress_image(data)
            url = s3_service.upload_to_s3(path, compressed, "image/jpeg")
            return path, url

        return await loop.run_in_executor(None, _do)

    results = await asyncio.gather(
        *[_upload_one(fn, d) for fn, d in files],
        return_exceptions=True,
    )

    for (fn, _data), res in zip(files, results):
        if isinstance(res, Exception):
            fail += 1
            last_error = str(res)
            continue
        s3_path, s3_url = res
        if s3_service.is_configured() and not s3_url:
            fail += 1
            last_error = "Ошибка S3"
            continue
        work = Work(
            user_id=user["user_id"],
            work_type=work_type,
            month=month,
            year=year,
            filename=fn,
            s3_url=s3_url,
            s3_path=s3_path,
            subject=subject,
            tariff=user.get("tariff"),
            student_score=student_score,
            status="success",
            drive_status="s3_only",
            cycle_id=cycle_id,
            is_final=is_final,
            parent_work_id=parent_work_id,
            attempt_number=attempt_number if is_final else None,
        )
        db.add(work)
        db.add(UploadLog(
            user_id=user["user_id"],
            student_name=user["name"],
            tariff=user.get("tariff"),
            month=month,
            photo_type=work_type,
            photo_count=1,
            status="success",
        ))
        db.flush()
        created_ids.append(work.id)
        success += 1

    if success > 0:
        db.commit()
    return success, fail, last_error, created_ids


# ── POST /upload/probnik/final ───────────────────────────────────────────────

@router.post("/upload/probnik/final")
async def upload_probnik_final(
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

    ticket = _has_active_ticket(db, user["user_id"], subject)
    if not ticket:
        return JSONResponse({"success": False, "error": f"Нет активного билета по предмету «{subject}»"}, status_code=404)

    # Lock: один открытый цикл на (user, subject) одновременно
    existing_lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == user["user_id"],
        MockExamLock.subject == subject,
        MockExamLock.is_locked == True,  # noqa: E712
    ).first()
    if existing_lock:
        return JSONResponse({
            "success": False,
            "error": f"Пробник по «{subject}» уже сдан в текущем цикле. Дождись обратной связи куратора.",
        }, status_code=409)

    files, err = await _read_photos(photos, max_files=1)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    cycle, created = get_or_create_cycle_for_probnik(
        db, user_id=user["user_id"], subject=subject, ticket_id=ticket.id,
    )
    attempt = next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM)

    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]

    def _path(fn: str) -> str:
        return s3_service.s3_path_probnik_cycle(
            user["vk_id"], cycle.id, attempt, "final", fn, user.get("tariff") or "",
        )

    success, fail, last_error, created_ids = await _upload_and_save(
        db=db, user=user, files=files,
        work_type=WORK_TYPE_MOCK_EXAM,
        cycle_id=cycle.id, attempt_number=attempt, is_final=True,
        parent_work_id=None, subject=subject, student_score=None,
        s3_path_builder=_path, month=month, year=now.year,
    )

    if success > 0:
        # Lock: блокируем повторную загрузку до закрытия цикла куратором
        lock = db.query(MockExamLock).filter(
            MockExamLock.user_id == user["user_id"],
            MockExamLock.subject == subject,
        ).first()
        if lock:
            lock.is_locked = True
            lock.locked_at = datetime.now(timezone.utc)
        else:
            db.add(MockExamLock(
                user_id=user["user_id"],
                subject=subject,
                is_locked=True,
                locked_at=datetime.now(timezone.utc),
            ))
        # Закрываем активную попытку этого предмета (старый MockExamAttempt flow)
        db.query(MockExamAttempt).filter(
            MockExamAttempt.user_id == user["user_id"],
            MockExamAttempt.subject == subject,
            MockExamAttempt.completed_at.is_(None),
        ).update({"completed_at": datetime.now(timezone.utc)}, synchronize_session=False)
        db.commit()

    return JSONResponse({
        "success": success > 0,
        "created": success,
        "failed": fail,
        "cycle_id": cycle.id,
        "cycle_created": created,
        "attempt_number": attempt,
        "work_ids": created_ids,
        "error": last_error if fail and not success else None,
    })


# ── POST /upload/probnik/intermediate ────────────────────────────────────────

@router.post("/upload/probnik/intermediate")
async def upload_probnik_intermediate(
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    parent_work_id: int = Form(...),
):
    parent = (
        db.query(Work)
        .filter(
            Work.id == parent_work_id,
            Work.user_id == user["user_id"],
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == True,  # noqa: E712
        )
        .first()
    )
    if not parent or not parent.cycle_id or parent.attempt_number is None:
        return JSONResponse({"success": False, "error": "Сначала загрузи финальную Пробника"}, status_code=400)

    existing = (
        db.query(Work)
        .filter(Work.parent_work_id == parent.id, Work.is_final == False)  # noqa: E712
        .count()
    )
    if existing >= MAX_INTERMEDIATE_PER_FINAL:
        return JSONResponse({"success": False, "error": f"Максимум {MAX_INTERMEDIATE_PER_FINAL} промежуточных на финальную"}, status_code=422)

    max_files = MAX_INTERMEDIATE_PER_FINAL - existing
    files, err = await _read_photos(photos, max_files=max_files)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]

    def _path(fn: str) -> str:
        return s3_service.s3_path_probnik_cycle(
            user["vk_id"], parent.cycle_id, parent.attempt_number,
            "intermediate", fn, user.get("tariff") or "",
        )

    success, fail, last_error, created_ids = await _upload_and_save(
        db=db, user=user, files=files,
        work_type=WORK_TYPE_MOCK_EXAM,
        cycle_id=parent.cycle_id, attempt_number=None, is_final=False,
        parent_work_id=parent.id, subject=parent.subject, student_score=None,
        s3_path_builder=_path, month=month, year=now.year,
    )

    return JSONResponse({
        "success": success > 0,
        "created": success,
        "failed": fail,
        "work_ids": created_ids,
        "error": last_error if fail and not success else None,
    })


# ── POST /upload/otrabotka/final ─────────────────────────────────────────────

@router.post("/upload/otrabotka/final")
async def upload_otrabotka_final(
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    subject: str = Form(...),
    student_score: float | None = Form(default=None),
):
    fa, fm = is_feature_available(db, FEATURE_RETAKE)
    if not fa:
        return JSONResponse({"success": False, "error": fm or "Отработка закрыта"}, status_code=403)
    if subject not in MOCK_SUBJECTS:
        return JSONResponse({"success": False, "error": "Выберите предмет"}, status_code=422)

    cycle = find_latest_cycle(db, user["user_id"], subject)
    if not cycle:
        return JSONResponse({"success": False, "error": "Сначала пройди Пробник"}, status_code=400)

    if student_score is not None and not (0 <= student_score <= 100):
        return JSONResponse({"success": False, "error": "Балл должен быть от 0 до 100"}, status_code=422)

    files, err = await _read_photos(photos, max_files=1)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    attempt = next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_RETAKE)
    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]

    def _path(fn: str) -> str:
        return s3_service.s3_path_otrabotka_cycle(
            user["vk_id"], cycle.id, attempt, "final", fn, user.get("tariff") or "",
        )

    success, fail, last_error, created_ids = await _upload_and_save(
        db=db, user=user, files=files,
        work_type=WORK_TYPE_RETAKE,
        cycle_id=cycle.id, attempt_number=attempt, is_final=True,
        parent_work_id=None, subject=subject,
        student_score=int(round(student_score)) if student_score is not None else None,
        s3_path_builder=_path, month=month, year=now.year,
    )

    return JSONResponse({
        "success": success > 0,
        "created": success,
        "failed": fail,
        "cycle_id": cycle.id,
        "attempt_number": attempt,
        "work_ids": created_ids,
        "error": last_error if fail and not success else None,
    })


# ── POST /upload/otrabotka/intermediate ──────────────────────────────────────

@router.post("/upload/otrabotka/intermediate")
async def upload_otrabotka_intermediate(
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    parent_work_id: int = Form(...),
):
    parent = (
        db.query(Work)
        .filter(
            Work.id == parent_work_id,
            Work.user_id == user["user_id"],
            Work.work_type == WORK_TYPE_RETAKE,
            Work.is_final == True,  # noqa: E712
        )
        .first()
    )
    if not parent or not parent.cycle_id or parent.attempt_number is None:
        return JSONResponse({"success": False, "error": "Сначала загрузи финальную Отработки"}, status_code=400)

    existing = (
        db.query(Work)
        .filter(Work.parent_work_id == parent.id, Work.is_final == False)  # noqa: E712
        .count()
    )
    if existing >= MAX_INTERMEDIATE_PER_FINAL:
        return JSONResponse({"success": False, "error": f"Максимум {MAX_INTERMEDIATE_PER_FINAL} промежуточных на финальную"}, status_code=422)

    max_files = MAX_INTERMEDIATE_PER_FINAL - existing
    files, err = await _read_photos(photos, max_files=max_files)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]

    def _path(fn: str) -> str:
        return s3_service.s3_path_otrabotka_cycle(
            user["vk_id"], parent.cycle_id, parent.attempt_number,
            "intermediate", fn, user.get("tariff") or "",
        )

    success, fail, last_error, created_ids = await _upload_and_save(
        db=db, user=user, files=files,
        work_type=WORK_TYPE_RETAKE,
        cycle_id=parent.cycle_id, attempt_number=None, is_final=False,
        parent_work_id=parent.id, subject=parent.subject, student_score=None,
        s3_path_builder=_path, month=month, year=now.year,
    )

    return JSONResponse({
        "success": success > 0,
        "created": success,
        "failed": fail,
        "work_ids": created_ids,
        "error": last_error if fail and not success else None,
    })
