"""Обратная связь по TaskBlockSubmission по проверенному паттерну домашки."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import IntegrityError

from app.cache import invalidate_unread
from app.models.notification import Notification
from app.models.task_block import TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.services import s3 as s3_service
from app.services.feedback import ROLE_STUDENT, role_from_rank, role_label_ru
from app.services.utils import compress_image

logger = logging.getLogger(__name__)
MAX_FEEDBACK_PHOTO_STORED_SIZE = 10 * 1024 * 1024


def get_or_create_feedback(
    db: DBSession, *, submission_id: int, initiator_id: int,
) -> tuple[TaskBlockFeedback, bool]:
    feedback = db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission_id
    ).first()
    if feedback is not None:
        return feedback, False
    feedback = TaskBlockFeedback(submission_id=submission_id, curator_id=initiator_id)
    try:
        # SAVEPOINT сохраняет внешнюю транзакцию пригодной к работе, если два
        # запроса одновременно создают самый первый диалог одной сдачи.
        with db.begin_nested():
            db.add(feedback)
            db.flush()
        return feedback, True
    except IntegrityError:
        existing = db.query(TaskBlockFeedback).filter(
            TaskBlockFeedback.submission_id == submission_id
        ).first()
        if existing is None:
            raise
        return existing, False


async def _upload_photo(
    submission_id: int, filename: str, data: bytes,
) -> tuple[str, str] | None:
    loop = asyncio.get_running_loop()
    path = s3_service.s3_path_task_block_feedback(submission_id, filename)

    def _do() -> tuple[bytes, str | None]:
        compressed = compress_image(data)
        if len(compressed) > MAX_FEEDBACK_PHOTO_STORED_SIZE:
            raise ValueError("Фото после сжатия превышает 10 МБ")
        return compressed, s3_service.upload_to_s3(path, compressed, "image/jpeg")

    try:
        _, url = await loop.run_in_executor(None, _do)
    except ValueError:
        raise
    except Exception as exc:
        logger.warning(
            "task block feedback photo upload failed for submission_id=%s: %s",
            submission_id, exc,
        )
        raise ValueError("Не удалось загрузить фото. Попробуй ещё раз.") from exc
    if not url:
        raise ValueError("Не удалось загрузить фото. Попробуй ещё раз.")
    return path, (url or "")


async def send_message(
    db: DBSession, *, feedback: TaskBlockFeedback, sender_id: int, sender_role: str,
    text: str | None, photo: tuple[str, bytes] | None, video_link: str | None = None,
) -> TaskBlockFeedbackMessage:
    text_clean = (text or "").strip() or None
    photo_path = None
    photo_url = None
    if photo is not None:
        uploaded = await _upload_photo(feedback.submission_id, photo[0], photo[1])
        if uploaded is not None:
            photo_path, photo_url = uploaded
    if text_clean is None and photo_url is None and not video_link:
        raise ValueError("Сообщение должно содержать текст, фото или ссылку на видео")
    message = TaskBlockFeedbackMessage(
        feedback_id=feedback.id,
        sender_id=sender_id,
        sender_role=sender_role,
        text=text_clean,
        photo_s3_path=photo_path,
        photo_s3_url=photo_url,
        video_url=video_link,
    )
    db.add(message)
    db.flush()
    return message


def notify_counterpart(
    db: DBSession, *, submission: TaskBlockSubmission, recipient_id: int,
    sender_role: str,
) -> Notification:
    title = (
        "Ученик ответил по сданной работе"
        if sender_role == ROLE_STUDENT
        else "Преподаватель оставил обратную связь по работе"
    )
    notification = Notification(
        user_id=recipient_id,
        title=title,
        text=f"По сданной работе #{submission.id} есть новое сообщение.",
        task_block_submission_id=submission.id,
    )
    db.add(notification)
    db.flush()
    invalidate_unread(recipient_id)
    return notification


def serialize_messages(
    messages: list[TaskBlockFeedbackMessage], names: dict[int, str] | None = None,
) -> list[dict]:
    names = names or {}
    return [
        {
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_role": message.sender_role,
            "sender_name": names.get(message.sender_id),
            "sender_role_label": role_label_ru(message.sender_role),
            "text": message.text,
            "photo_s3_url": message.photo_s3_url,
            "video_url": message.video_url,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        }
        for message in messages
    ]


__all__ = [
    "get_or_create_feedback", "notify_counterpart", "role_from_rank",
    "send_message", "serialize_messages",
]
