"""Оценка и диалог по работам, сданным внутри блоков задания."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.db.database import get_db
from app.dependencies import (
    require_admin_role,
    require_csrf,
    require_csrf_header,
    require_curator,
    require_scorer,
    require_student,
    require_work_revision_sender,
)
from app.models.feedback_rating import DIALOG_TASK_BLOCK
from app.models.notification import Notification
from app.models.task_block import TaskBlock, TaskBlockSubmission, TaskBlockSubmissionImage
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
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
from app.services.linked_feedback import (
    absorbed_main_block,
    main_submission_for,
    work_group,
)
from app.services.feedback_edit import (
    EditError,
    apply_edit,
    delete_task_block_feedback,
    edited_feedback_notification,
    editable_task_block_message_ids,
    finish_task_block_revision,
    request_task_block_revision,
    revision_request_notification,
    task_block_delete_refusal,
    task_block_edit_refusal,
    task_block_return_refusal,
)
from app.services.notify import notify
from app.services.rbac import can_score, can_send_work_to_revision
from app.services.section_access import can
from app.services.student_access import get_student_for_staff_access
from app.services.task_block_feedback import (
    close_refusal,
    draft_messages,
    get_or_create_feedback,
    notify_counterpart,
    send_drafts,
    role_from_rank,
    send_message,
    serialize_messages,
    student_can_reply,
)
from app.services.submission_changes import change_history
from app.services.task_blocks import list_submission_images
from app.services.upload_validation import read_image_uploads
from app.services.utils import validate_video_link
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")
MAX_FEEDBACK_PHOTO_INPUT_SIZE = 25 * 1024 * 1024


REVIEW_SECTIONS = {
    "task": "Задание",
    "composition": "Композиционное решение",
    "structure": "Конструктивное решение",
    "graphics": "Графическое решение",
    "recommendations": "Рекомендации",
}


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


def _locked_feedback(db: DBSession, submission_id: int) -> TaskBlockFeedback | None:
    # Блокировка совпадает с отправкой и закрытием ОС. После ожидания читаем
    # свежий статус даже если диалог уже есть в identity map запроса.
    return db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission_id,
    ).populate_existing().with_for_update().first()


def _editable_text_draft(message: TaskBlockFeedbackMessage) -> bool:
    return bool(
        message.is_draft and message.sender_role != "student" and message.text
        and not (message.photo_s3_url or message.video_url or message.video_s3_url or message.audio_s3_url)
    )


def _draft_revision(message: TaskBlockFeedbackMessage) -> str:
    stamp = message.edited_at or message.created_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return sha256(
        dumps([
            message.id, stamp.isoformat(), message.text, message.review_image_id,
            message.review_section, message.review_submission_id,
        ]).encode("utf-8")
    ).hexdigest()


def _autosave_context(messages: list[TaskBlockFeedbackMessage], author_id: int) -> dict:
    own = [message for message in messages if message.sender_id == author_id]
    return {
        "cursor": max((message.id for message in own), default=0),
        "drafts": [
            {"id": message.id, "text": message.text, "revision": _draft_revision(message),
             "image_id": message.review_image_id, "section": message.review_section,
             "target_submission_id": message.review_submission_id}
            for message in own if _editable_text_draft(message)
        ],
    }


def _mark_notifications_read(db: DBSession, user_id: int, submission_id: int) -> None:
    db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.task_block_submission_id == submission_id,
        Notification.is_read.is_(False),
    ).update(
        {"is_read": True, "read_at": datetime.now(timezone.utc)},
        synchronize_session=False,
    )


def _revision_state(db: DBSession, feedback: TaskBlockFeedback | None, user: dict) -> dict:
    """Возврат завершённой ОС автору на правку: что показать сотруднику.
    Вернуть могут ГП и суперадмин, завершить правку — автор или они же."""
    if feedback is None:
        return {"on": False, "can_return": False, "can_finish": False, "can_delete": False}
    on = feedback.is_on_revision
    is_head = user.get("role_rank", 1) >= 4
    return {
        "on": on,
        "can_return": is_head and task_block_return_refusal(db, feedback) is None,
        "can_finish": on and (is_head or feedback.curator_id == user["user_id"]),
        # Удалить ошибочную ОС целиком (21.10.2026) — те же ГП и суперадмин.
        "can_delete": is_head and task_block_delete_refusal(db, feedback) is None,
    }


def _review_choices(db: DBSession, submission: TaskBlockSubmission, task: TrackerTask):
    group = work_group(db, submission)
    submission_images = list_submission_images(db, submission.id)
    if group:
        work_choices = {
            work.submission.id: " · ".join(filter(None, (
                work.task_title, work.block_title, work.submitted_label,
            )))
            for work in group.works
        }
        image_choices = {
            image.id: (f"{work.task_title} · Фото {number}", image.image_s3_url, work.submission.id)
            for work in group.works
            for number, image in enumerate(
                submission_images if work.submission.id == submission.id
                else list_submission_images(db, work.submission.id), 1
            )
        }
    else:
        work_choices = {submission.id: task.title or "Текущая работа"}
        image_choices = {
            image.id: (f"Фото {number}", image.image_s3_url, submission.id)
            for number, image in enumerate(submission_images, 1)
        }
    return group, submission_images, work_choices, image_choices


def _render(
    request: Request, db: DBSession, *, submission: TaskBlockSubmission,
    user: dict, viewer_role: str, student: User | None,
) -> HTMLResponse:
    block, task = _context(db, submission)
    feedback = _feedback(db, submission.id)
    awaits_group = absorbed_main_block(db, submission) is not None
    messages = feedback.messages if feedback else []
    if viewer_role == "student":
        # Черновик ОС ученику не виден, пока не нажата «Отправить ОС»
        # (владелец 22.10.2026).
        messages = [message for message in messages if not message.is_draft]
    sender_ids = {message.sender_id for message in messages}
    names = {
        row.id: row.name for row in db.query(User).filter(User.id.in_(sender_ids)).all()
    } if sender_ids else {}
    _mark_notifications_read(db, user["user_id"], submission.id)
    db.commit()
    invalidate_unread(user["user_id"])
    back_url = (
        f"/cabinet/students?student={submission.user_id}&tab=tasks"
        if viewer_role != "student" else "/cabinet/learning"
    )
    past_works = []
    queue_navigation = None
    if viewer_role != "student":
        from app.services.task_blocks import submission_review_queue

        past_works = [
            row for row in submission_review_queue(db, student_id=submission.user_id, limit=12)
            if row["submission_id"] != submission.id and row["submitted_at"] < submission.submitted_at
        ][:4] if submission.submitted_at else []
        if (user.get("nav_rank") or user["role_rank"]) == 2 and can(user, "students_review", "view"):
            from app.services.curator_review_queue import review_neighbors

            queue_navigation = review_neighbors(db, user["user_id"], submission, request.query_params)
    group, submission_images, work_choices, image_choices = _review_choices(db, submission, task)
    return templates.TemplateResponse(request, "task_block_feedback_detail.html", {
        "request": request,
        "user": user,
        "viewer_role": viewer_role,
        "autosave": (
            _autosave_context(messages, user["user_id"])
            if viewer_role != "student" and not is_closed(feedback)
            and not awaits_group and can(user, "students_review") else None
        ),
        # Балл ставит только ГП и выше; куратор видит его только для чтения.
        "can_score": viewer_role != "student" and can_score(user.get("role_rank", 1)),
        # «Вернуть на доработку» — только здесь, в диалоге (владелец 14.10.2026),
        # куратор и выше (`rbac.WORK_REVISION_MIN_RANK`). Раздел проверки —
        # как у кнопок проверки: модератору его не положено, и кнопка ответила бы 403.
        "can_send_revision": (
            viewer_role != "student" and can(user, "students_review")
            and can_send_work_to_revision(user.get("role_rank", 1))
        ),
        "student": student,
        "submission": submission,
        "past_works": past_works,
        "review_images": image_choices,
        "review_works": work_choices,
        "review_sections": REVIEW_SECTIONS,
        "queue_navigation": queue_navigation,
        # Общая ОС на несколько заданий (13.10.2026): прошлые работы ученика,
        # на которые этот ответ тоже. None — работа одна.
        "group": group,
        # Работа сама входит в общую ОС, а главная ещё не сдана: ответа по
        # ней отдельно не будет (`linked_feedback`).
        "awaits_group": awaits_group,
        "submission_images": submission_images,
        # Сколько и когда ученик менял сданную работу и что было до правки
        # (владелец 07.10.2026) — для проверяющего, ученику не показываем.
        "change_history": (
            change_history(db, submission) if viewer_role != "student" else None
        ),
        "block": block,
        "task": task,
        "messages": serialize_messages(messages, names),
        # Правка своей ОС (05.10.2026) — одно правило на роут и экран.
        "editable_message_ids": (
            editable_task_block_message_ids(db, feedback, user["user_id"])
            if viewer_role != "student" else set()
        ),
        "revision": _revision_state(db, feedback, user) if viewer_role != "student" else None,
        # Может ли ученик ответить — одно правило на роут и экран (01.10.2026).
        "reply": student_can_reply(db, submission, feedback),
        # Сколько сообщений ждут «Отправить ОС» (владелец 22.10.2026).
        "draft_count": len(draft_messages(feedback)) if viewer_role != "student" else 0,
        # ОС, фаза 2: «Завершить ОС» и оценка ученика (О10–О26). Когда
        # завершать — `close_refusal` (22.10.2026), одно правило с роутом.
        "rating_panel": rating_panel(
            db, dialog_kind=DIALOG_TASK_BLOCK, dialog=feedback, viewer_role=viewer_role,
            close_url=f"/cabinet/staff/task-block-submissions/{submission.id}/close-feedback",
            rating_url=f"/cabinet/task-block-submissions/{submission.id}/rating",
            close_refusal=close_refusal(db, submission, feedback),
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
    main = main_submission_for(db, submission)
    if main is not None:
        # Работа вошла в общую ОС — ответ пишется там (созвон 13.10.2026).
        url = f"/cabinet/staff/task-block-submissions/{main.id}/feedback"
        if request.url.query:
            url += "?" + request.url.query
        return RedirectResponse(url, status_code=302)
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
    main = main_submission_for(db, submission)
    if main is not None:
        # Ответ по этой работе — общий, в диалоге главной (13.10.2026).
        return RedirectResponse(
            f"/cabinet/task-block-submissions/{main.id}/feedback", status_code=302,
        )
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
    if submission.needs_revision:
        raise HTTPException(status_code=409, detail="Сначала дождитесь новой сдачи")
    previous_score = int(submission.score) if submission.score is not None else None
    submission.score = payload.score
    submission.scored_at = datetime.now(timezone.utc)
    submission.scored_by_id = user["user_id"]
    notification = None
    if previous_score != payload.score:
        block, task = _context(db, submission)
        # Ученик сдаёт работы сразу по нескольким заданиям и предметам — без
        # предмета и названия балл в уведомлении не к чему привязать.
        subject = block.subject or task.subject
        where = f"{subject}, «{task.title}»" if subject else f"«{task.title}»"
        notification = Notification(
            user_id=submission.user_id,
            title="Преподаватель оценил работу",
            text=f"{where} – {payload.score} / 100.",
            task_block_submission_id=submission.id,
        )
        db.add(notification)
        db.flush()
        invalidate_unread(submission.user_id)
    db.commit()
    if notification is not None:
        background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True, "score": payload.score})


@router.post("/staff/task-block-submissions/{submission_id}/revision", response_class=JSONResponse)
def send_submission_to_revision(
    submission_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_work_revision_sender)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    comment: str = Form(default=""),
):
    """Открыть повторную загрузку конкретной работы, сохранив её диалог."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    if submission.needs_revision:
        raise HTTPException(status_code=409, detail="Работа уже на доработке")
    comment_clean = comment.strip()
    if len(comment_clean) > 2000:
        raise HTTPException(status_code=422, detail="Комментарий слишком длинный")
    submission.needs_revision = True
    submission.needs_revision_at = datetime.now(timezone.utc)
    submission.reviewed_at = None
    submission.reviewed_by_id = None
    submission.score = None
    submission.scored_at = None
    submission.scored_by_id = None
    if comment_clean:
        submission.review_comment = comment_clean
    notification = Notification(
        user_id=submission.user_id,
        title="Работу нужно загрузить заново",
        text=(f"{comment_clean}\n\nЗагрузи правильное фото в этом задании."
              if comment_clean else "Загрузи правильное фото в этом задании."),
        task_block_submission_id=submission.id,
    )
    db.add(notification)
    db.flush()
    invalidate_unread(submission.user_id)
    db.commit()
    background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True})


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
    draft: TaskBlockFeedbackMessage | None = None,
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
            video_link=video_link_clean, video_is_note=video_note == "1", draft=draft,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)

    sender_role = role_from_rank(user.get("role_rank", 1))
    notification = None
    # Сообщение сотрудника — черновик: ученику его открывает и одним
    # уведомлением сообщает «Отправить ОС» (владелец 22.10.2026; до этого
    # уведомление уходило на каждое сообщение, по 5–15 на одну ОС).
    if sender_role == "student" and feedback.curator_id != user["user_id"]:
        notification = notify_counterpart(
            db, submission=submission, recipient_id=feedback.curator_id,
            sender_role=sender_role,
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
    draft_id: int | None = Form(default=None),
    draft_revision: str = Form(default=""),
):
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    if absorbed_main_block(db, submission) is not None:
        # Иначе у работы завёлся бы свой диалог рядом с общим — два ответа
        # на одну работу (открытая до связки вкладка, старая ссылка).
        return JSONResponse(
            {"ok": False, "error": "Эта работа входит в общую обратную связь – ответ пишется там."},
            status_code=409,
        )
    feedback, _ = get_or_create_feedback(
        db, submission_id=submission.id, initiator_id=user["user_id"],
    )
    feedback = _locked_feedback(db, submission.id) or feedback
    if is_closed(feedback):
        # «Завершить ОС» закрывает диалог для обеих сторон (О25).
        return JSONResponse(
            {"ok": False, "error": "Обратная связь завершена – диалог закрыт."},
            status_code=403,
        )
    draft = None
    if draft_id is not None:
        draft = db.get(TaskBlockFeedbackMessage, draft_id)
        if (draft is None or draft.feedback_id != feedback.id
                or draft.sender_id != user["user_id"] or not _editable_text_draft(draft)
                or _draft_revision(draft) != draft_revision):
            return _error(409, "Черновик изменился. Обновите страницу перед добавлением вложений.")
    return await _post_message(
        submission=submission, feedback=feedback, db=db, user=user, text=text,
        photo=photo, video_link=video_link, background_tasks=background_tasks,
        video=video, audio=audio, video_note=video_note, draft=draft,
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


def _is_review_target(db: DBSession, main: TaskBlockSubmission, target_id: int) -> bool:
    if target_id == main.id:
        return True
    target = db.get(TaskBlockSubmission, target_id)
    if not target or not target.submitted_at or target.user_id != main.user_id:
        return False
    owner = main_submission_for(db, target)
    return owner is not None and owner.id == main.id


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=20000)
    message_id: int | None = Field(default=None, gt=0)
    revision: str | None = Field(default=None, min_length=64, max_length=64)
    cursor: int = Field(ge=0)
    image_id: int | None = Field(default=None, gt=0)
    target_submission_id: int | None = Field(default=None, gt=0)
    section: Literal["task", "composition", "structure", "graphics", "recommendations"] | None = None


def _draft_response(message: TaskBlockFeedbackMessage | None, cursor: int) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "id": message.id if message is not None else None,
        "revision": _draft_revision(message) if message is not None else None,
        "cursor": cursor,
    })


@router.put("/staff/task-block-submissions/{submission_id}/draft", response_class=JSONResponse)
def save_feedback_draft(
    submission_id: int,
    data: DraftUpdate,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Сохранить текстовое замечание. Ученику оно откроется по «Отправить ОС»."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    if not can(user, "students_review"):
        return _error(403, "Нет доступа к проверке работ.")
    if absorbed_main_block(db, submission) is not None:
        return _error(409, "Откройте общую обратную связь, чтобы сохранить замечание к этой работе.")
    if "\x00" in data.text:
        return _error(422, "В тексте замечания есть недопустимый символ.")

    author_id = user["user_id"]
    content = data.text if data.text.strip() else ""
    feedback = _locked_feedback(db, submission.id)
    if feedback is None and content and data.message_id is None and data.cursor == 0:
        get_or_create_feedback(db, submission_id=submission.id, initiator_id=author_id)
        feedback = _locked_feedback(db, submission.id)
    if is_closed(feedback):
        return _error(403, "Обратная связь завершена, диалог закрыт.")
    if feedback is None:
        if not content and data.message_id is None and data.cursor == 0:
            return _draft_response(None, 0)
        return _error(409, "Диалог изменился. Обновите страницу перед сохранением.")

    own_query = db.query(TaskBlockFeedbackMessage).filter(
        TaskBlockFeedbackMessage.feedback_id == feedback.id,
        TaskBlockFeedbackMessage.sender_id == author_id,
    )
    newest = own_query.order_by(TaskBlockFeedbackMessage.id.desc()).first()
    cursor = newest.id if newest else 0
    message = db.get(TaskBlockFeedbackMessage, data.message_id) if data.message_id else None
    if message is not None and (message.feedback_id != feedback.id or message.sender_id != author_id):
        return _error(409, "Черновик больше недоступен. Обновите страницу.")
    metadata = data.model_dump(exclude_unset=True)
    image_id = metadata.get("image_id", message.review_image_id if message else None)
    target_id = metadata.get("target_submission_id", message.review_submission_id if message else None)
    section = metadata.get("section", message.review_section if message else None)
    if content and target_id is not None and (message is None or target_id != message.review_submission_id):
        if not _is_review_target(db, submission, target_id):
            return _error(422, "Выберите работу из этого диалога.")
    if content and image_id is not None and (
        message is None or image_id != message.review_image_id or target_id != message.review_submission_id
    ):
        image = db.get(TaskBlockSubmissionImage, image_id)
        if image is None:
            return _error(422, "Фото больше недоступно.")
        if not _is_review_target(db, submission, image.submission_id):
            return _error(422, "Выберите фото работы из этого диалога.")
        if target_id is not None and image.submission_id != target_id:
            return _error(422, "Выберите фото указанной работы.")

    def matches(candidate: TaskBlockFeedbackMessage) -> bool:
        return (
            candidate.text == content and candidate.review_image_id == image_id
            and candidate.review_section == section and candidate.review_submission_id == target_id
        )

    if data.message_id is None:
        if cursor != data.cursor:
            # Если ответ первого PUT потерялся, повтор не создаёт второе сообщение.
            # Отправленное или изменённое сообщение повтором не открываем заново.
            if (
                newest is not None and cursor > data.cursor and _editable_text_draft(newest)
                and matches(newest) and own_query.filter(
                    TaskBlockFeedbackMessage.id > data.cursor,
                ).count() == 1
            ):
                return _draft_response(newest, cursor)
            return _error(409, "Диалог изменился. Обновите страницу перед сохранением.")
        if not content:
            return _draft_response(None, cursor)
        message = TaskBlockFeedbackMessage(
            feedback_id=feedback.id, sender_id=author_id,
            sender_role=role_from_rank(user["role_rank"]), text=content, is_draft=True,
            review_image_id=image_id, review_section=section, review_submission_id=target_id,
        )
        db.add(message)
        db.flush()
        cursor = message.id
    else:
        if message is None and not content and cursor <= data.cursor:
            return _draft_response(None, cursor)
        if message is None:
            return _error(409, "Черновик больше недоступен. Обновите страницу.")
        if not _editable_text_draft(message):
            return _error(409, "Сообщение уже отправлено или изменилось. Обновите страницу.")
        if matches(message):
            return _draft_response(message, cursor)
        if data.revision != _draft_revision(message):
            return _error(409, "Замечание изменилось в другом окне. Обновите страницу перед сохранением.")
        if content:
            message.text = content
            message.review_image_id = image_id
            message.review_section = section
            message.review_submission_id = target_id
            message.edited_at = datetime.now(timezone.utc)
        else:
            db.delete(message)
            db.flush()
            newest = own_query.order_by(TaskBlockFeedbackMessage.id.desc()).first()
            cursor = newest.id if newest else 0
            message = None
    response = _draft_response(message, cursor)
    db.commit()
    return response


@router.post("/staff/task-block-submissions/{submission_id}/send-feedback", response_class=JSONResponse)
def send_feedback(
    submission_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
    preview: bool = False,
    snapshot: str | None = None,
):
    """Предпросмотр и отправка всех черновиков выбранного диалога."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    if (preview or snapshot is not None) and (
        not can(user, "students_review") or absorbed_main_block(db, submission) is not None
    ):
        return _error(403, "Этот диалог недоступен для отправки.")
    # Отправка, предпросмотр и autosave используют одну блокировку диалога.
    feedback = _locked_feedback(db, submission.id)
    if is_closed(feedback):
        return _error(403, "Обратная связь завершена – диалог закрыт.")
    if preview or snapshot is not None:
        drafts = serialize_messages(draft_messages(feedback))
        if not drafts:
            return _error(409, "Новых сообщений нет – отправлять нечего.")
        _, task = _context(db, submission)
        _, _, works, images = _review_choices(db, submission, task)
        items = [
            {
                "text": row["text"],
                "section": REVIEW_SECTIONS.get(row["review_section"]),
                "work": works.get(row["review_submission_id"], "Работа больше недоступна")
                if row["review_submission_id"] else None,
                "image": images.get(row["review_image_id"], ("Фото больше недоступно",))[0]
                if row["review_image_id"] else None,
                "attachments": [
                    label for field, label in (
                        ("photo_s3_url", "Фото"), ("video_s3_url", "Видео"),
                        ("video_url", "Ссылка на видео"), ("audio_s3_url", "Голосовое"),
                    ) if row[field]
                ],
            }
            for row in drafts
        ]
        current_snapshot = sha256(dumps([drafts, items], sort_keys=True).encode("utf-8")).hexdigest()
        if preview:
            return JSONResponse({"ok": True, "items": items, "snapshot": current_snapshot})
        if current_snapshot != snapshot:
            return _error(409, "Черновики изменились. Откройте предпросмотр ещё раз.")
    if not send_drafts(feedback):
        return _error(409, "Новых сообщений нет – отправлять нечего.")
    notification = notify_counterpart(
        db, submission=submission, recipient_id=submission.user_id,
        sender_role=role_from_rank(user.get("role_rank", 1)),
    )
    db.commit()
    background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True})


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
    if not is_closed(feedback):
        # Завершают, когда ученику больше нечего спросить (22.10.2026).
        refusal = close_refusal(db, submission, feedback)
        if refusal:
            return _error(409, refusal)
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


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


@router.post(
    "/staff/task-block-submissions/{submission_id}/messages/{message_id}/edit",
    response_class=JSONResponse,
)
def edit_staff_message(
    submission_id: int,
    message_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    text: str = Form(default=""),
    remove: list[str] = Form(default=[]),
):
    """Сотрудник правит своё сообщение ОС: текст и «убрать вложение»
    (владелец 05.10.2026). Кто и когда — `feedback_edit.task_block_edit_refusal`.
    Правка на возврате (ОС уже завершена) — ученику уведомление; пока ОС
    открыта, хватает пометки «изменено»."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    feedback = _locked_feedback(db, submission.id)
    message = db.get(TaskBlockFeedbackMessage, message_id)
    if feedback is None or message is None or message.feedback_id != feedback.id:
        return _error(404, "Сообщение не найдено.")
    refusal = task_block_edit_refusal(db, feedback, message, user["user_id"])
    if refusal:
        return _error(403, refusal)
    try:
        changed = apply_edit(message, text=text, remove=set(remove))
    except EditError as exc:
        return _error(exc.status_code, exc.message)
    notification = None
    if changed and feedback.feedback_closed_at is not None:
        _block, task = _context(db, submission)
        notification = edited_feedback_notification(
            db, student_id=submission.user_id, subject=f"«{task.title}»",
            link_path=_student_link(submission.id), task_block_submission_id=submission.id,
        )
    db.commit()
    if notification is not None:
        background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True})


@router.post(
    "/staff/task-block-submissions/{submission_id}/return-to-author",
    response_class=JSONResponse,
)
def return_feedback_to_author(
    submission_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """ГП или суперадмин возвращает завершённую ОС автору на правку
    (владелец 05.10.2026). Диалог остаётся закрытым, автор только правит
    свои сообщения. Оценённую ОС не возвращают — её уже не правят."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    _block, task = _context(db, submission)
    feedback = db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission.id
    ).with_for_update().first()
    refusal = task_block_return_refusal(db, feedback)
    if refusal:
        return _error(409, refusal)
    request_task_block_revision(feedback)
    notification = None
    if feedback.curator_id != user["user_id"]:
        notification = revision_request_notification(
            db, author_id=feedback.curator_id, subject=f"«{task.title}»",
            link_path=f"/cabinet/staff/task-block-submissions/{submission.id}/feedback",
            task_block_submission_id=submission.id,
        )
    db.commit()
    if notification is not None:
        background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True})


@router.post(
    "/staff/task-block-submissions/{submission_id}/delete-feedback",
    response_class=JSONResponse,
)
def delete_feedback(
    submission_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """ГП или суперадмин удаляет ошибочную ОС целиком (владелец 21.10.2026):
    диалог, уведомления о нём, отметку «проверено». Только пока ОС не
    завершена, не оценена и ученик не ответил — `task_block_delete_refusal`.
    Ученику ничего не сообщается."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    feedback = db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission.id
    ).with_for_update().first()
    if feedback is None:
        return _error(404, "Обратная связь не найдена.")
    refusal = task_block_delete_refusal(db, feedback)
    if refusal:
        return _error(409, refusal)
    delete_task_block_feedback(db, feedback, submission, user_id=user["user_id"])
    db.commit()
    return JSONResponse({"ok": True})


@router.post(
    "/staff/task-block-submissions/{submission_id}/revision-done",
    response_class=JSONResponse,
)
def finish_feedback_revision(
    submission_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """«Завершить правку»: автор ОС, ГП или суперадмин снимает возврат."""
    submission = _submission_or_404(db, submission_id)
    _staff_guard(db, user, submission)
    feedback = _feedback(db, submission.id)
    if feedback is None:
        return _error(404, "Обратная связь не найдена.")
    if feedback.curator_id != user["user_id"] and user.get("role_rank", 1) < 4:
        return _error(403, "Завершить правку может автор обратной связи.")
    if not finish_task_block_revision(feedback):
        return _error(409, "Обратная связь не на правке.")
    db.commit()
    return JSONResponse({"ok": True})


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
