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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.constants import MONTHS, MOCK_SUBJECTS, FEATURE_RETAKE
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.exam_assignment import ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.mock_exam_lock import MockExamLock
from app.models.notification import Notification
from app.models.upload_log import UploadLog
from app.models.work import (
    Work,
    WORK_TYPE_MOCK_EXAM,
    WORK_TYPE_RETAKE,
)
from app.services import s3 as s3_service
from app.services.exam_cycle import (
    MAX_INTERMEDIATE_PER_FINAL,
    close_or_expire_mock_exam_attempts,
    count_cycle_intermediates,
    cycle_submission_state,
    find_latest_cycle,
    get_active_tickets,
    get_unsubmitted_active_tickets,
    get_or_create_cycle_for_probnik,
    has_closed_cycle_for_ticket,
    has_submitted_for_ticket,
    intermediate_upload_state,
    next_attempt_number,
)
from app.services.feature_periods import is_feature_available
from app.services.mock_exam_access import (
    is_mock_exam_attempt_open,
    ticket_closes_at,
    ticket_duration_sec,
)
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
        try:
            with db.begin_nested():
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
        except IntegrityError:
            # Гонка: два параллельных запроса одновременно сдавали финал одного
            # и того же цикла — uq_works_cycle_final (БД) пропустил только
            # первого. Второй проигрывает гонку и должен сообщить об этом, а не
            # упасть 500м или молча создать дубликат.
            fail += 1
            last_error = "работа уже сдана (параллельная отправка)"
            continue
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


def _get_open_mock_attempt(
    db: DBSession, *, user_id: int, subject: str, ticket: ExamTicket
) -> MockExamAttempt | None:
    attempt = (
        db.query(MockExamAttempt)
        .filter(
            MockExamAttempt.user_id == user_id,
            MockExamAttempt.subject == subject,
            MockExamAttempt.ticket_id == ticket.id,
            MockExamAttempt.completed_at.is_(None),
            MockExamAttempt.expired_at.is_(None),
        )
        .order_by(MockExamAttempt.started_at.desc())
        .first()
    )
    if not attempt:
        return None
    if is_mock_exam_attempt_open(
        attempt.started_at,
        closes_at=ticket_closes_at(ticket),
        duration_sec=ticket_duration_sec(ticket),
    ):
        return attempt
    attempt.expired_at = datetime.now(timezone.utc)
    db.commit()
    return None


def _get_ticket_with_open_mock_attempt(
    db: DBSession, *, user_id: int, subject: str
) -> tuple[ExamTicket | None, MockExamAttempt | None]:
    for ticket in get_unsubmitted_active_tickets(db, user_id, subject):
        attempt = _get_open_mock_attempt(
            db, user_id=user_id, subject=subject, ticket=ticket
        )
        if attempt is not None:
            return ticket, attempt
    return None, None


def _submitted_current_ticket(
    db: DBSession, *, user_id: int, subject: str
) -> ExamTicket | None:
    for ticket in get_active_tickets(db, user_id, subject):
        if has_submitted_for_ticket(db, user_id, subject, ticket.id):
            return ticket
    return None


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

    active_tickets = get_active_tickets(db, user["user_id"], subject)
    if not active_tickets:
        return JSONResponse({"success": False, "error": "Сдача пробника сейчас недоступна"}, status_code=404)
    available_tickets = get_unsubmitted_active_tickets(db, user["user_id"], subject)

    # Блок повторной сдачи: финал по любому билету текущего задания закрывает весь
    # пробник. Исключение — staff явно вернул работу «на доработку»
    # (needs_revision=True): такой финал не считается сдачей, поэтому redo проходит.
    # Сообщение различает закрытый цикл (оценён) и открытый (ждёт ОС).
    if not available_tickets:
        submitted_ticket = _submitted_current_ticket(
            db, user_id=user["user_id"], subject=subject
        )
        if submitted_ticket is None:
            submitted_ticket = active_tickets[0]
        ticket = submitted_ticket
        closed = has_closed_cycle_for_ticket(db, user["user_id"], subject, ticket.id)
        return JSONResponse(
            {
                "success": False,
                "error": "пробник уже оценён и закрыт" if closed
                else "работа сдана, ждите обратной связи",
            },
            status_code=409,
        )
    # Таймер «Начать пробник» гейтит сдачу: без открытой попытки финал не принимается
    # (перезалив без revision больше не разрешён, поэтому исключений нет).
    ticket, open_attempt = _get_ticket_with_open_mock_attempt(
        db, user_id=user["user_id"], subject=subject
    )
    if ticket is None or open_attempt is None:
        return JSONResponse(
            {
                "success": False,
                "error": "Сначала нажмите «Начать пробник». После выдачи билета есть заданное время на сдачу.",
            },
            status_code=403,
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

    # Перезалив: уведомить уже вовлечённый staff (балл сейчас молча сбросится в
    # _overwrite_final, проверяющий должен узнать о новом фото). Цель — тот, кто уже
    # оценивал (scored_by_id), и автор диалога ОС (Feedback.curator_id). Захватываем
    # ДО перезаписи, т.к. _overwrite_final обнуляет scored_by_id.
    resubmit_recipients: set[int] = set()
    if existing_final is not None:
        if existing_final.scored_by_id:
            resubmit_recipients.add(existing_final.scored_by_id)
        fb_curator = (
            db.query(Feedback.curator_id)
            .filter(Feedback.work_id == existing_final.id)
            .scalar()
        )
        if fb_curator:
            resubmit_recipients.add(fb_curator)
        resubmit_recipients.discard(user["user_id"])

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

        submission_state = cycle_submission_state(
            db,
            cycle_id=cycle.id,
            work_type=WORK_TYPE_MOCK_EXAM,
        )
        if not submission_state["verified"]:
            db.commit()
            return JSONResponse({
                "success": False,
                "created": success,
                "failed": fail,
                "cycle_id": cycle.id,
                "cycle_created": created,
                "attempt_number": attempt,
                "work_ids": created_ids,
                "error": "Финальное фото не подтверждено в базе. Проверьте соединение и попробуйте отправить ещё раз.",
                **submission_state,
            }, status_code=500)

        # Перезалив: оповестить вовлечённый staff о новом фото (балл сброшен →
        # нужна повторная проверка). На первой сдаче recipients пуст → тихо.
        if resubmit_recipients and final_id:
            for rid in resubmit_recipients:
                db.add(Notification(
                    user_id=rid,
                    title="Ученик перезагрузил работу пробника",
                    text=f"{user['name']} загрузил новое фото пробника по «{subject}» — нужна повторная проверка.",
                    work_id=final_id,
                ))
                invalidate_unread(rid)

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
    else:
        submission_state = cycle_submission_state(
            db,
            cycle_id=cycle.id,
            work_type=WORK_TYPE_MOCK_EXAM,
        )

    return JSONResponse({
        "success": success > 0 and bool(submission_state["verified"]),
        "created": success,
        "failed": fail,
        "cycle_id": cycle.id,
        "cycle_created": created,
        "attempt_number": attempt,
        "work_ids": created_ids,
        "error": last_error if fail and not success else None,
        **submission_state,
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

    active_tickets = get_active_tickets(db, user["user_id"], subject)
    if not active_tickets:
        return JSONResponse({"success": False, "error": "Сдача пробника сейчас недоступна"}, status_code=404)
    available_tickets = get_unsubmitted_active_tickets(db, user["user_id"], subject)
    # Этапные привязаны к той же сдаче, что и финал: как только по билету сдан финал,
    # докидывать фото нельзя (перезалив закрыт — см. final-роут). В нормальном потоке
    # этапные грузятся ДО финального (has_submitted_for_ticket ещё False), поэтому
    # первая сдача не страдает; исключение — redo после «на доработку» (needs_revision).
    if not available_tickets:
        submitted_ticket = _submitted_current_ticket(
            db, user_id=user["user_id"], subject=subject
        )
        if submitted_ticket is None:
            submitted_ticket = active_tickets[0]
        ticket = submitted_ticket
        closed = has_closed_cycle_for_ticket(db, user["user_id"], subject, ticket.id)
        return JSONResponse(
            {
                "success": False,
                "error": "пробник уже оценён и закрыт" if closed
                else "работа сдана, ждите обратной связи",
            },
            status_code=409,
        )
    ticket, open_attempt = _get_ticket_with_open_mock_attempt(
        db, user_id=user["user_id"], subject=subject
    )
    if ticket is None or open_attempt is None:
        return JSONResponse(
            {
                "success": False,
                "error": "Сначала нажмите «Начать пробник». После выдачи билета есть заданное время на сдачу.",
            },
            status_code=403,
        )

    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=user["user_id"], subject=subject, ticket_id=ticket.id,
    )
    attempt_number = next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM)

    existing = (
        count_cycle_intermediates(
            db,
            cycle_id=cycle.id,
            work_type=WORK_TYPE_MOCK_EXAM,
        )
    )
    upload_state = intermediate_upload_state(existing)
    if existing >= MAX_INTERMEDIATE_PER_FINAL:
        return JSONResponse({
            "success": False,
            "error": f"Лимит этапных фото исчерпан: уже загружено {existing} из {MAX_INTERMEDIATE_PER_FINAL}",
            **upload_state,
        }, status_code=422)

    max_files = upload_state["remaining"]
    if len(photos) > max_files:
        return JSONResponse({
            "success": False,
            "error": (
                f"Можно добавить ещё {max_files} этапных фото: "
                f"уже загружено {existing} из {MAX_INTERMEDIATE_PER_FINAL}"
            ),
            **upload_state,
        }, status_code=422)
    files, err = await _read_photos(photos, max_files=max_files)
    if err:
        return JSONResponse({"success": False, "error": err, **upload_state}, status_code=422)

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
    upload_state = intermediate_upload_state(
        count_cycle_intermediates(
            db,
            cycle_id=cycle.id,
            work_type=WORK_TYPE_MOCK_EXAM,
        )
    )

    return JSONResponse({
        "success": success > 0,
        "created": success,
        "failed": fail,
        "work_ids": created_ids,
        "error": last_error if fail and not success else None,
        **upload_state,
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
