"""Оценка и диалог по работам, сданным внутри блоков задания."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.db.database import get_db
from app.dependencies import (
    require_csrf,
    require_csrf_header,
    require_curator,
    require_scorer,
    require_student,
)
from app.models.feedback_rating import DIALOG_TASK_BLOCK
from app.models.notification import Notification
from app.models.task_block import TaskBlock, TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback
from app.models.tracker import TrackerTask
from app.models.user import User
from app.services.feedback import (  # общие проверки вложений, не завязаны на Work
    read_audio_upload,
    read_video_upload,
)
from app.services.feedback_rating import (
    RatingError,
    close_dialog,
    create_rating,
    feedback_type_for_block,
    is_closed,
    rate_request_notification,
    read_rating_form,
    rating_panel,
    send_rating_to_care_topic,
)
from app.services.notify import notify
from app.services.rbac import can_score
from app.services.student_access import get_student_for_staff_access
from app.services.task_block_feedback import (
    get_or_create_feedback,
    notify_counterpart,
    role_from_rank,
    send_message,
    serialize_messages,
    student_can_reply,
)
from app.services.task_blocks import list_submission_images
from app.services.upload_validation import read_image_uploads
from app.services.utils import validate_video_link
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")
MAX_FEEDBACK_PHOTO_INPUT_SIZE = 25 * 1024 * 1024


def _submission_or_404(db: DBSession, submission_id: int) -> TaskBlockSubmission:
    submission = db.get(TaskBlockSubmission, submission_id)
    if submission is None or submission.submitted_at is None:
        raise HTTPException(status_code=404, detail="Сдача не найдена")
    return submission


def _staff_guard(db: DBSession, user: dict, submission: TaskBlockSubmission) -> User:
    return get_student_for_staff_access(
        db, user, submission.user_id,
        not_found_detail="Сдача не найдена", forbidden_detail="Это не ваш студент",
    )


def _student_guard(user: dict, submission: TaskBlockSubmission) -> None:
    if submission.user_id != user["user_id"]:
        raise HTTPException(status_code=404, detail="Сдача не найдена")


def _context(db: DBSession, submission: TaskBlockSubmission) -> tuple[TaskBlock, TrackerTask]:
    block = db.get(TaskBlock, submission.block_id)
    task = db.get(TrackerTask, block.task_id) if block else None
    if block is None or task is None:
        raise HTTPException(status_code=404, detail="Сдача не найдена")
    return block, task


def _feedback(db: DBSession, submission_id: int) -> TaskBlockFeedback | None:
    return db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission_id
    ).first()


def _mark_notifications_read(db: DBSession, user_id: int, submission_id: int) -> None:
    db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.task_block_submission_id == submission_id,
        Notification.is_read.is_(False),
    ).update(
        {"is_read": True, "read_at": datetime.now(timezone.utc)},
        synchronize_session=False,
    )


def _render(
    request: Request, db: DBSession, *, submission: TaskBlockSubmission,
    user: dict, viewer_role: str, student: User | None,
) -> HTMLResponse:
    block, task = _context(db, submission)
    feedback = _feedback(db, submission.id)
    messages = feedback.messages if feedback else []
    sender_ids = {message.sender_id for message in messages}
    names = {
        row.id: row.name for row in db.query(User).filter(User.id.in_(sender_ids)).all()
    } if sender_ids else {}
    _mark_notifications_read(db, user["user_id"], submission.id)
    db.commit()
    invalidate_unread(user["user_id"])
    back_url = (
        f"/cabinet/staff/students-review/{submission.user_id}"
        if viewer_role != "student" else "/cabinet/learning"
    )
    return templates.TemplateResponse(request, "task_block_feedback_detail.html", {
        "request": request,
        "user": user,
        "viewer_role": viewer_role,
        # Балл ставит только ГП и выше; куратор видит его только для чтения.
        "can_score": viewer_role != "student" and can_score(user.get("role_rank", 1)),
        "student": student,
        "submission": submission,
        "submission_images": list_submission_images(db, submission.id),
        "block": block,
        "task": task,
        "messages": serialize_messages(messages, names),
        # Может ли ученик ответить — одно правило на роут и экран (01.10.2026).
        "reply": student_can_reply(db, submission, feedback),
        # ОС, фаза 2: «Завершить ОС» и оценка ученика (О10–О26).
        "rating_panel": rating_panel(
            db, dialog_kind=DIALOG_TASK_BLOCK, dialog=feedback, viewer_role=viewer_role,
            close_url=f"/cabinet/staff/task-block-submissions/{submission.id}/close-feedback",
            rating_url=f"/cabinet/task-block-submissions/{submission.id}/rating",
        ),
        "back_url": back_url,
    })


@router.get("/staff/task-block-submissions/{submission_id}/feedback", response_class=HTMLResponse)
def staff_feedback_page(
    submission_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    submission = _submission_or_404(db, submission_id)
    student = _staff_guard(db, user, submission)
    return _render(
        request, db, submission=submission, user=user,
        viewer_role=role_from_rank(user.get("role_rank", 1)), student=student,
    )


@router.get("/task-block-submissions/{submission_id}/feedback", response_class=HTMLResponse)
def student_feedback_page(
    submission_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    submission = _submission_or_404(db, submission_id)
    _student_guard(user, submission)
    return _render(
        request, db, submission=submission, user=user, viewer_role="student", student=None,
    )


class ScorePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=0, le=100)


@router.post("/staff/task-block-submissions/{submission_id}/score", response_class=JSONResponse)
def score_submission(
    submission_id: int,
    payload: ScorePayload,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_scorer)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Балл за любую сдачу в задании (домашка, контрольная на время, загрузка,
    «фото + сдача») — только ГП и выше (`rbac.SCORE_MIN_RANK`, 30.09.2026).
    Куратор проверяет работу отметкой «проверено» и диалогом."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    previous_score = int(submission.score) if submission.score is not None else None
    submission.score = payload.score
    submission.scored_at = datetime.now(timezone.utc)
    submission.scored_by_id = user["user_id"]
    notification = None
    if previous_score != payload.score:
        notification = Notification(
            user_id=submission.user_id,
            title="Преподаватель оценил работу",
            text=f"Оценка за сданную работу: {payload.score} / 100.",
            task_block_submission_id=submission.id,
        )
        db.add(notification)
        db.flush()
        invalidate_unread(submission.user_id)
    db.commit()
    if notification is not None:
        background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True, "score": payload.score})


async def _photo_payload(photo: UploadFile | None) -> tuple[str, bytes] | None:
    if photo is None or not photo.filename:
        return None
    files, error = await read_image_uploads(
        [photo], max_files=1, max_size=MAX_FEEDBACK_PHOTO_INPUT_SIZE,
    )
    if error:
        raise ValueError(error)
    return files[0]


async def _post_message(
    *, submission: TaskBlockSubmission, feedback: TaskBlockFeedback,
    db: DBSession, user: dict, text: str, photo: UploadFile | None,
    video_link: str, background_tasks: BackgroundTasks,
    video: UploadFile | None = None, audio: UploadFile | None = None,
    video_note: str = "",
) -> JSONResponse:
    try:
        photo_data = await _photo_payload(photo)
        video_data = await read_video_upload(video)
        audio_data = await read_audio_upload(audio)
        video_link_clean = validate_video_link(video_link)
        await send_message(
            db, feedback=feedback, sender_id=user["user_id"],
            sender_role=role_from_rank(user.get("role_rank", 1)), text=text,
            photo=photo_data, video=video_data, audio=audio_data,
            video_link=video_link_clean, video_is_note=video_note == "1",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)

    sender_role = role_from_rank(user.get("role_rank", 1))
    recipient_id = submission.user_id if sender_role != "student" else feedback.curator_id
    notification = None
    if recipient_id != user["user_id"]:
        notification = notify_counterpart(
            db, submission=submission, recipient_id=recipient_id, sender_role=sender_role,
        )
    db.commit()
    if notification is not None:
        background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True})


@router.post("/staff/task-block-submissions/{submission_id}/messages", response_class=JSONResponse)
async def staff_message(
    submission_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    text: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
    video_link: str = Form(default=""),
    video: UploadFile | None = File(default=None),
    audio: UploadFile | None = File(default=None),
    video_note: str = Form(default=""),
):
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    feedback, _ = get_or_create_feedback(
        db, submission_id=submission.id, initiator_id=user["user_id"],
    )
    if is_closed(feedback):
        # «Завершить ОС» закрывает диалог для обеих сторон (О25).
        return JSONResponse(
            {"ok": False, "error": "Обратная связь завершена – диалог закрыт."},
            status_code=403,
        )
    return await _post_message(
        submission=submission, feedback=feedback, db=db, user=user, text=text,
        photo=photo, video_link=video_link, background_tasks=background_tasks,
        video=video, audio=audio, video_note=video_note,
    )


@router.post("/task-block-submissions/{submission_id}/messages", response_class=JSONResponse)
async def student_message(
    submission_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    text: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
    video_link: str = Form(default=""),
    video: UploadFile | None = File(default=None),
    audio: UploadFile | None = File(default=None),
):
    submission = _submission_or_404(db, submission_id)
    _student_guard(user, submission)
    # Строка диалога под блокировкой до коммита: два одновременных ответа
    # иначе оба насчитали бы «осталось одно сообщение» и прошли бы мимо
    # лимита (на SQLite в тестах `FOR UPDATE` молча пропускается).
    feedback = db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission.id
    ).with_for_update().first()
    reply = student_can_reply(db, submission, feedback)
    if not reply.allowed:
        # JSON с `error`, а не HTTPException: форма показывает ученику
        # именно это поле (`task_block_feedback_detail.html`).
        return JSONResponse({"ok": False, "error": reply.student_text}, status_code=403)
    return await _post_message(
        submission=submission, feedback=feedback, db=db, user=user, text=text,
        photo=photo, video_link=video_link, background_tasks=background_tasks,
        video=video, audio=audio,
    )


def _student_link(submission_id: int) -> str:
    return f"/cabinet/task-block-submissions/{submission_id}/feedback"


@router.post("/staff/task-block-submissions/{submission_id}/close-feedback", response_class=JSONResponse)
def close_feedback(
    submission_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """«Завершить ОС» (ОС, фаза 2, О25): диалог закрыт для обеих сторон,
    ученику одно уведомление «оцени ОС» (О26). Повтор ничего не делает."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    _block, task = _context(db, submission)
    # Блокировка строки: два одновременных нажатия иначе прислали бы
    # ученику два уведомления.
    feedback = db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission.id
    ).with_for_update().first()
    try:
        closed_now = close_dialog(feedback, user["user_id"])
    except RatingError as exc:
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status_code)
    notification = None
    if closed_now:
        notification = rate_request_notification(
            db, student_id=submission.user_id, subject=f"«{task.title}»",
            link_path=_student_link(submission.id), task_block_submission_id=submission.id,
        )
    db.commit()
    if notification is not None:
        background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True, "closed_now": closed_now})


@router.post("/task-block-submissions/{submission_id}/rating", response_class=JSONResponse)
async def student_rating(
    submission_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    score: str = Form(default=""),
    comment: str = Form(default=""),
    screenshots: list[UploadFile] | None = File(default=None),
):
    """Оценка ОС учеником: 1–5, комментарий обязателен, до трёх скриншотов,
    одна на диалог и без правок (О10–О14, О24). После коммита — сообщение в
    служебный топик (О17, О18)."""
    submission = _submission_or_404(db, submission_id)
    _student_guard(user, submission)
    block, task = _context(db, submission)
    feedback = _feedback(db, submission.id)
    try:
        value, text, images = await read_rating_form(score, comment, screenshots)
        rating = await create_rating(
            db, dialog_kind=DIALOG_TASK_BLOCK, dialog=feedback, student_id=submission.user_id,
            feedback_type=feedback_type_for_block(block.block_type),
            task_id=task.id, task_title=task.title,
            score=value, comment=text, screenshots=images,
        )
    except RatingError as exc:
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status_code)
    db.commit()
    background_tasks.add_task(send_rating_to_care_topic, rating.id)
    return JSONResponse({"ok": True})
