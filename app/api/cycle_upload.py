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
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import MONTHS, MOCK_SUBJECTS, FEATURE_RETAKE
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.exam_cycle import ExamCycle
from app.models.mock_exam_lock import MockExamLock
from app.models.upload_log import UploadLog
from app.models.work import (
    Work,
    WORK_TYPE_MOCK_EXAM,
    WORK_TYPE_RETAKE,
)
from app.services import s3 as s3_service
from app.services.exam_cycle import (
    close_or_expire_mock_exam_attempts,
    find_latest_cycle,
    get_active_ticket,
    get_or_create_cycle_for_probnik,
    has_submitted_for_ticket,
    next_attempt_number,
)
from app.services.feature_periods import is_feature_available
from app.services.upload_validation import (
    MAX_UPLOAD_FILE_SIZE,
    MAX_UPLOAD_FILES,
    read_image_uploads,
)
from app.services.utils import compress_image

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_SIZE = MAX_UPLOAD_FILE_SIZE
MAX_FILES = MAX_UPLOAD_FILES
MAX_INTERMEDIATE_PER_FINAL = 10


async def _read_photos(photos: list[UploadFile], *, max_files: int) -> tuple[list[tuple[str, bytes]], str | None]:
    return await read_image_uploads(
        photos,
        max_files=max_files,
        max_size=MAX_SIZE,
    )


async def _upload_cycle_file_to_s3(
    filename: str,
    data: bytes,
    s3_path_builder: Callable[[str], str],
) -> tuple[str, str | None]:
    """Compress and upload one cycle file; Work/UploadLog contracts stay in callers."""
    loop = asyncio.get_running_loop()
    path = s3_path_builder(filename)

    def _do() -> tuple[str, str | None]:
        compressed = compress_image(data)
        url = s3_service.upload_to_s3(path, compressed, "image/jpeg")
        return path, url

    return await loop.run_in_executor(None, _do)


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
    success = 0
    fail = 0
    last_error = ""
    created_ids: list[int] = []

    results = await asyncio.gather(
        *[_upload_cycle_file_to_s3(fn, d, s3_path_builder) for fn, d in files],
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


def _find_existing_final(db: DBSession, *, cycle_id: int, work_type: str) -> Work | None:
    """Текущий финал цикла данного типа (для перезаписи). В цикле он один.

    Не проверяет closed_at: для Отработки повторная отправка перезаписывает
    финал даже в закрытом цикле (последняя работа всегда побеждает).
    """
    return (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle_id,
            Work.work_type == work_type,
            Work.is_final == True,  # noqa: E712
        )
        .order_by(Work.id.desc())
        .first()
    )


async def _overwrite_final(
    *,
    db: DBSession,
    user: dict,
    final: Work,
    file: tuple[str, bytes],
    subject: str | None,
    student_score: float | None,
    s3_path_builder,
    month: str,
    year: int,
) -> tuple[int, int, str, list[int]]:
    """Перезаписать существующий финал новым фото (in-place).

    Сохраняем Work.id, cycle_id, attempt_number и привязанные этапные
    (parent_work_id) — перезапись касается только самого финала. Сбрасываем
    оценку: новое фото ещё не проверено.
    """
    fn, data = file

    try:
        s3_path, s3_url = await _upload_cycle_file_to_s3(fn, data, s3_path_builder)
    except Exception as exc:  # noqa: BLE001
        return 0, 1, str(exc), []
    if s3_service.is_configured() and not s3_url:
        return 0, 1, "Ошибка S3", []

    final.filename = fn
    final.s3_url = s3_url
    final.s3_path = s3_path
    final.month = month
    final.year = year
    if subject is not None:
        final.subject = subject
    final.student_score = student_score
    final.status = "success"
    final.drive_status = "s3_only"
    final.score = None
    final.scored_at = None
    final.scored_by_id = None
    final.comment = None
    final.needs_revision = False
    final.created_at = datetime.now(timezone.utc)
    db.add(UploadLog(
        user_id=user["user_id"],
        student_name=user["name"],
        tariff=user.get("tariff"),
        month=month,
        photo_type=final.work_type,
        photo_count=1,
        status="success",
    ))
    db.commit()
    return 1, 0, "", [final.id]


# ── POST /upload/probnik/final ───────────────────────────────────────────────

@router.post("/upload/probnik/final")
async def upload_probnik_final(
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    subject: str = Form(...),
):
    if subject not in MOCK_SUBJECTS:
        return JSONResponse({"success": False, "error": "Выберите предмет"}, status_code=422)

    ticket = get_active_ticket(db, user["user_id"], subject)
    if not ticket:
        return JSONResponse({"success": False, "error": "Сдача пробника сейчас недоступна"}, status_code=404)

    # Одна сдача на билет: пробник по предмету закрыт от первой сдачи и до выдачи
    # СЛЕДУЮЩЕГО билета. Если по текущему билету уже есть цикл — открытый (работа
    # ждёт ОС) или закрытый (уже оценён) — повторная сдача запрещена. Новый билет
    # (новый ticket_id) → цикла ещё нет → сдача открывается заново.
    if has_submitted_for_ticket(db, user["user_id"], subject, ticket.id):
        return JSONResponse(
            {"success": False, "error": "работа сдана, ждите ОС"},
            status_code=409,
        )

    files, err = await _read_photos(photos, max_files=1)
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=422)

    cycle, created = get_or_create_cycle_for_probnik(
        db, user_id=user["user_id"], subject=subject, ticket_id=ticket.id,
    )
    existing_final = _find_existing_final(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM)
    attempt = (
        existing_final.attempt_number
        if existing_final is not None
        else next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM)
    )

    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]

    def _path(fn: str) -> str:
        return s3_service.s3_path_probnik_cycle(
            user["vk_id"], cycle.id, attempt, "final", fn, user.get("tariff") or "",
        )

    if existing_final is not None:
        success, fail, last_error, created_ids = await _overwrite_final(
            db=db, user=user, final=existing_final, file=files[0],
            subject=subject, student_score=None,
            s3_path_builder=_path, month=month, year=now.year,
        )
    else:
        success, fail, last_error, created_ids = await _upload_and_save(
            db=db, user=user, files=files,
            work_type=WORK_TYPE_MOCK_EXAM,
            cycle_id=cycle.id, attempt_number=attempt, is_final=True,
            parent_work_id=None, subject=subject, student_score=None,
            s3_path_builder=_path, month=month, year=now.year,
        )

    if success > 0:
        # Привязать этапные, загруженные до финального (parent_work_id=None)
        final_id = created_ids[0] if created_ids else (existing_final.id if existing_final else None)
        if final_id:
            db.query(Work).filter(
                Work.cycle_id == cycle.id,
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.is_final == False,  # noqa: E712
                Work.parent_work_id.is_(None),
            ).update({"parent_work_id": final_id}, synchronize_session=False)

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
        # MockExamAttempt — снимок билета на момент «Начать пробник» (legacy flow).
        # Закрываем попытку ТЕКУЩЕГО билета как «сдано» (safety-net для legacy
        # /upload/mock-exam/start по тому же билету); открытые попытки ДРУГИХ
        # (старых) билетов этого предмета — устаревшие снимки, помечаем expired_at.
        close_or_expire_mock_exam_attempts(db, user["user_id"], subject, ticket.id)
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
    subject: str = Form(...),
):
    if subject not in MOCK_SUBJECTS:
        return JSONResponse({"success": False, "error": "Выберите предмет"}, status_code=422)

    ticket = get_active_ticket(db, user["user_id"], subject)
    if not ticket:
        return JSONResponse({"success": False, "error": "Сдача пробника сейчас недоступна"}, status_code=404)

    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=user["user_id"], subject=subject, ticket_id=ticket.id,
    )
    attempt_number = next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM)

    existing = (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle.id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == False,  # noqa: E712
        )
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
            user["vk_id"], cycle.id, attempt_number,
            "intermediate", fn, user.get("tariff") or "",
        )

    success, fail, last_error, created_ids = await _upload_and_save(
        db=db, user=user, files=files,
        work_type=WORK_TYPE_MOCK_EXAM,
        cycle_id=cycle.id, attempt_number=None, is_final=False,
        parent_work_id=None, subject=subject, student_score=None,
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

    existing_final = _find_existing_final(db, cycle_id=cycle.id, work_type=WORK_TYPE_RETAKE)
    attempt = (
        existing_final.attempt_number
        if existing_final is not None
        else next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_RETAKE)
    )
    now = datetime.now(timezone.utc)
    month = MONTHS[now.month - 1]
    norm_score = int(round(student_score)) if student_score is not None else None

    def _path(fn: str) -> str:
        return s3_service.s3_path_otrabotka_cycle(
            user["vk_id"], cycle.id, attempt, "final", fn, user.get("tariff") or "",
        )

    if existing_final is not None:
        success, fail, last_error, created_ids = await _overwrite_final(
            db=db, user=user, final=existing_final, file=files[0],
            subject=subject, student_score=norm_score,
            s3_path_builder=_path, month=month, year=now.year,
        )
    else:
        success, fail, last_error, created_ids = await _upload_and_save(
            db=db, user=user, files=files,
            work_type=WORK_TYPE_RETAKE,
            cycle_id=cycle.id, attempt_number=attempt, is_final=True,
            parent_work_id=None, subject=subject,
            student_score=norm_score,
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
