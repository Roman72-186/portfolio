"""Сдача домашней работы учеником + обратная связь по ней.

Контракт загрузки — как у пробника (`app/api/cycle_upload.py`), но без
билета/попытки/цикла: ровно одно финальное фото, до
`MAX_INTERMEDIATE_PER_SUBMISSION` промежуточных, пересдача перезаписывает
финал. Диалог обратной связи — по образцу `Feedback`, но собственной моделью
(`app/models/homework_feedback.py`) — причины в докстроке той модели.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import TARIFFS_WITH_FEEDBACK
from app.db.database import get_db
from app.dependencies import require_csrf, require_curator, require_student
from app.models.homework import HomeworkAssignment
from app.models.homework_feedback import HomeworkFeedback
from app.models.homework_submission import STATUS_ACCEPTED, HomeworkSubmission
from app.models.tracker import ITEM_HOMEWORK, SOURCE_HOMEWORK, TrackerTask
from app.models.user import User
from app.services import s3 as s3_service
from app.services.homework_feedback import (
    get_or_create_feedback,
    notify_counterpart,
    role_from_rank,
    send_message as send_feedback_message,
    serialize_messages,
)
from app.services.homework_submission import (
    MAX_INTERMEDIATE_PER_SUBMISSION,
    add_intermediate_image,
    count_intermediate_images,
    get_or_create_submission,
    get_submission,
    list_images,
    list_submissions_for_task,
    set_final_image,
)
from app.services.tracker import accessible_task_ids, close_task_for_user
from app.services.tracker import homework_images as list_homework_reference_images
from app.services.upload_validation import read_image_uploads
from app.services.utils import compress_image
from app.services.video_topics import accessible_topic_ids
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")

MAX_UPLOAD_FILE_SIZE = 10 * 1024 * 1024


def _resolve_homework_task(db: DBSession, task_id: int) -> tuple[TrackerTask, HomeworkAssignment]:
    task = db.get(TrackerTask, task_id)
    if (
        task is None
        or task.deleted_at is not None
        or not task.is_published
        or task.kind != ITEM_HOMEWORK
        or task.source_kind != SOURCE_HOMEWORK
        or task.source_id is None
    ):
        raise HTTPException(status_code=404, detail="Задание не найдено")
    homework = db.get(HomeworkAssignment, task.source_id)
    if homework is None or homework.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return task, homework


def _guard_student_access(db: DBSession, task: TrackerTask, user_id: int) -> None:
    """Та же проверка, что у `cabinet_tracker_toggle` — не дублировать запрос."""
    accessible = (
        task.topic_id is not None and task.topic_id in accessible_topic_ids(db, user_id)
    ) or (task.topic_id is None and task.id in accessible_task_ids(db, user_id))
    if not accessible:
        raise HTTPException(status_code=404, detail="Задание не найдено")


def _submission_intermediate_limit(homework: HomeworkAssignment) -> int:
    """`max_files` — сколько файлов ждёт куратор; 0 читаем как «без объявленного
    лимита», но технический потолок всё равно есть — тот же, что у пробника."""
    if homework.max_files and homework.max_files > 0:
        return min(homework.max_files, MAX_INTERMEDIATE_PER_SUBMISSION)
    return MAX_INTERMEDIATE_PER_SUBMISSION


def _viewer_role(user: dict) -> str:
    return role_from_rank(user.get("role_rank", 1))


async def _render_submission_page(
    request: Request, db: DBSession, *, task: TrackerTask, homework: HomeworkAssignment,
    submission: HomeworkSubmission, user: dict, viewer_role: str, back_url: str,
):
    images = list_images(db, submission.id)
    final_image = next((i for i in images if i.is_final), None)
    intermediate = [i for i in images if not i.is_final]

    fb = (
        db.query(HomeworkFeedback)
        .filter(HomeworkFeedback.submission_id == submission.id)
        .first()
    )
    messages = fb.messages if fb else []
    sender_ids = {m.sender_id for m in messages}
    names = {
        u.id: u.name
        for u in db.query(User).filter(User.id.in_(sender_ids)).all()
    } if sender_ids else {}
    student = db.get(User, submission.user_id) if viewer_role != "student" else None

    return templates.TemplateResponse("homework_submission.html", {
        "request": request,
        "student": student,
        "user": user,
        "viewer_role": viewer_role,
        "task": task,
        "homework": homework,
        "reference_images": list_homework_reference_images(db, homework.id),
        "submission": submission,
        "final_image": final_image,
        "intermediate_images": intermediate,
        "max_intermediate": _submission_intermediate_limit(homework),
        "messages": serialize_messages(messages, names),
        "back_url": back_url,
    })


# ── Ученик ────────────────────────────────────────────────────────────────

@router.get("/homework/{task_id}", response_class=HTMLResponse)
async def student_homework_page(
    task_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    task, homework = _resolve_homework_task(db, task_id)
    _guard_student_access(db, task, user["user_id"])
    submission, _ = get_or_create_submission(db, task=task, user_id=user["user_id"])
    db.commit()
    return await _render_submission_page(
        request, db, task=task, homework=homework, submission=submission,
        user=user, viewer_role="student", back_url="/cabinet/learning",
    )


@router.post("/homework/{task_id}/final", response_class=JSONResponse)
async def upload_homework_final(
    task_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photo: UploadFile = File(...),
):
    task, homework = _resolve_homework_task(db, task_id)
    _guard_student_access(db, task, user["user_id"])
    submission, _ = get_or_create_submission(db, task=task, user_id=user["user_id"])

    files, err = await read_image_uploads([photo], max_files=1, max_size=MAX_UPLOAD_FILE_SIZE)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=422)
    filename, data = files[0]

    s3_path = s3_service.s3_path_homework_submission(
        user["vk_id"], submission.id, "final", filename, user.get("tariff") or "",
    )
    url = s3_service.upload_to_s3(s3_path, compress_image(data), "image/jpeg")
    if s3_service.is_configured() and not url:
        return JSONResponse({"ok": False, "error": "Ошибка загрузки в хранилище"}, status_code=502)

    set_final_image(db, submission, url=url or "", path=s3_path if url else None)
    # Тариф без обратной связи — закрываем сразу по факту загрузки, как видео;
    # с обратной связью — ждём, пока куратор нажмёт «Принять работу» (решение
    # владельца 23.08, см. plans/2026-08-23-apparchi-week-month-gate-decisions.md).
    if (user.get("tariff") or "") not in TARIFFS_WITH_FEEDBACK:
        close_task_for_user(db, task, user["user_id"], source="auto")
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/homework/{task_id}/intermediate", response_class=JSONResponse)
async def upload_homework_intermediate(
    task_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
):
    task, homework = _resolve_homework_task(db, task_id)
    _guard_student_access(db, task, user["user_id"])
    submission, _ = get_or_create_submission(db, task=task, user_id=user["user_id"])

    limit = _submission_intermediate_limit(homework)
    existing = count_intermediate_images(db, submission.id)
    if existing >= limit:
        return JSONResponse(
            {"ok": False, "error": f"Лимит фото исчерпан: уже загружено {existing} из {limit}"},
            status_code=422,
        )
    files, err = await read_image_uploads(
        photos, max_files=limit - existing, max_size=MAX_UPLOAD_FILE_SIZE,
    )
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=422)

    created = 0
    for filename, data in files:
        s3_path = s3_service.s3_path_homework_submission(
            user["vk_id"], submission.id, "intermediate", filename, user.get("tariff") or "",
        )
        url = s3_service.upload_to_s3(s3_path, compress_image(data), "image/jpeg")
        if s3_service.is_configured() and not url:
            continue
        add_intermediate_image(db, submission, url=url or "", path=s3_path if url else None)
        created += 1
    db.commit()
    return JSONResponse({"ok": True, "created": created})


# ── Обратная связь (ученик и куратор) ───────────────────────────────────────

async def _post_message(
    request: Request,
    submission: HomeworkSubmission,
    feedback: HomeworkFeedback,
    db: DBSession,
    user: dict,
    text: str | None,
    photo: UploadFile | None,
) -> JSONResponse:
    photo_payload = None
    if photo is not None and photo.filename:
        data = await photo.read()
        if data:
            photo_payload = (photo.filename, data)
    try:
        await send_feedback_message(
            db, feedback=feedback, sender_id=user["user_id"],
            sender_role=_viewer_role(user), text=text, photo=photo_payload,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)

    recipient_id = (
        submission.user_id if _viewer_role(user) != "student" else feedback.curator_id
    )
    if recipient_id and recipient_id != user["user_id"]:
        notify_counterpart(
            db, submission=submission, recipient_id=recipient_id,
            sender_role=_viewer_role(user),
        )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/homework/{task_id}/message", response_class=JSONResponse)
async def student_send_homework_message(
    task_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    text: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
):
    task, _ = _resolve_homework_task(db, task_id)
    _guard_student_access(db, task, user["user_id"])
    submission = get_submission(db, tracker_task_id=task.id, user_id=user["user_id"])
    if submission is None:
        raise HTTPException(status_code=404, detail="Сначала отправьте работу")
    # Тот же порядок, что у пробника: пока куратор не написал первым —
    # диалога ещё нет, ученику отвечать нечему.
    fb = (
        db.query(HomeworkFeedback)
        .filter(HomeworkFeedback.submission_id == submission.id)
        .first()
    )
    if fb is None:
        raise HTTPException(
            status_code=403, detail="Куратор ещё не ответил — дождитесь первого сообщения"
        )
    return await _post_message(request, submission, fb, db, user, text, photo)


# ── Куратор/staff ────────────────────────────────────────────────────────

@router.get("/staff/homework/{task_id}/submissions", response_class=HTMLResponse)
def staff_homework_submissions(
    task_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    task = db.get(TrackerTask, task_id)
    if task is None or task.kind != ITEM_HOMEWORK or task.source_kind != SOURCE_HOMEWORK:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    homework = db.get(HomeworkAssignment, task.source_id)
    submissions = list_submissions_for_task(db, task.id)
    student_ids = [s.user_id for s in submissions]
    students = {
        u.id: u for u in db.query(User).filter(User.id.in_(student_ids)).all()
    } if student_ids else {}
    rows = [
        {"submission": s, "student": students.get(s.user_id)}
        for s in submissions
    ]
    return templates.TemplateResponse("staff_homework_submissions.html", {
        "request": request,
        "user": user,
        "task": task,
        "homework": homework,
        "rows": rows,
    })


@router.get("/staff/homework/submissions/{submission_id}", response_class=HTMLResponse)
async def staff_homework_submission_detail(
    submission_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    submission = db.get(HomeworkSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Сдача не найдена")
    task = db.get(TrackerTask, submission.tracker_task_id)
    homework = db.get(HomeworkAssignment, submission.homework_id)
    if task is None or homework is None:
        raise HTTPException(status_code=404, detail="Сдача не найдена")
    return await _render_submission_page(
        request, db, task=task, homework=homework, submission=submission,
        user=user, viewer_role=_viewer_role(user),
        back_url=f"/cabinet/staff/homework/{task.id}/submissions",
    )


@router.post("/staff/homework/submissions/{submission_id}/accept", response_class=JSONResponse)
async def accept_homework_submission(
    submission_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Куратор принимает финальное фото — тариф с обратной связью закрывается
    только здесь, не по факту загрузки (решение владельца 23.08)."""
    submission = db.get(HomeworkSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Сдача не найдена")
    images = list_images(db, submission.id)
    if not any(i.is_final for i in images):
        raise HTTPException(status_code=409, detail="Финальное фото ещё не загружено")
    if submission.status != STATUS_ACCEPTED:
        submission.status = STATUS_ACCEPTED
        task = db.get(TrackerTask, submission.tracker_task_id)
        if task is not None:
            close_task_for_user(db, task, submission.user_id, source="staff")
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/staff/homework/submissions/{submission_id}/message", response_class=JSONResponse)
async def staff_send_homework_message(
    submission_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    text: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
):
    submission = db.get(HomeworkSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Сдача не найдена")
    fb, _ = get_or_create_feedback(db, submission_id=submission.id, initiator_id=user["user_id"])
    return await _post_message(request, submission, fb, db, user, text, photo)
