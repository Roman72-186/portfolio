"""Диалог обратной связи по домашке — по образцу `app/services/feedback.py`.

`role_from_rank`/`role_label_ru`/`serialize_messages`-эквиваленты у пробника
не завязаны на `Work`, поэтому берутся оттуда напрямую импортом, а не
копируются: `_upload_photo`/`_upload_video`/`notify_counterpart` завязаны на
`work_id`/`Work` и здесь переписаны под `submission_id`/`HomeworkSubmission`.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import HomeworkSubmission
from app.models.notification import Notification
from app.services import s3 as s3_service
from app.services.feedback import (  # переиспользование, не завязано на Work
    ROLE_STUDENT,
    role_from_rank,
    role_label_ru,
)
from app.services.utils import compress_image

logger = logging.getLogger(__name__)

MAX_FEEDBACK_PHOTO_STORED_SIZE = 10 * 1024 * 1024


def get_or_create_feedback(
    db: DBSession, *, submission_id: int, initiator_id: int
) -> tuple[HomeworkFeedback, bool]:
    fb = (
        db.query(HomeworkFeedback)
        .filter(HomeworkFeedback.submission_id == submission_id)
        .first()
    )
    if fb is not None:
        return fb, False
    fb = HomeworkFeedback(submission_id=submission_id, curator_id=initiator_id)
    db.add(fb)
    db.flush()
    return fb, True


async def _upload_photo(submission_id: int, filename: str, data: bytes) -> tuple[str, str] | None:
    loop = asyncio.get_running_loop()
    s3_path = s3_service.s3_path_homework_feedback(submission_id, filename)

    def _do() -> tuple[bytes, str | None]:
        compressed = compress_image(data)
        if len(compressed) > MAX_FEEDBACK_PHOTO_STORED_SIZE:
            raise ValueError("Фото после сжатия превышает 10 МБ")
        return compressed, s3_service.upload_to_s3(s3_path, compressed, "image/jpeg")

    try:
        _, s3_url = await loop.run_in_executor(None, _do)
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("homework feedback photo upload exception for submission_id=%s: %s", submission_id, exc)
        return None
    if s3_service.is_configured() and not s3_url:
        logger.warning("homework feedback photo upload failed for submission_id=%s", submission_id)
        return None
    return s3_path, (s3_url or "")


async def send_message(
    db: DBSession,
    *,
    feedback: HomeworkFeedback,
    sender_id: int,
    sender_role: str,
    text: str | None,
    photo: tuple[str, bytes] | None,
) -> HomeworkFeedbackMessage:
    """Создать сообщение в диалоге. Хотя бы одно из (text, photo). Без commit."""
    text_clean = (text or "").strip() or None
    photo_path: str | None = None
    photo_url: str | None = None
    if photo is not None:
        filename, data = photo
        uploaded = await _upload_photo(feedback.submission_id, filename, data)
        if uploaded is not None:
            photo_path, photo_url = uploaded
    if text_clean is None and photo_url is None:
        raise ValueError("Сообщение должно содержать текст или фото")

    msg = HomeworkFeedbackMessage(
        feedback_id=feedback.id,
        sender_id=sender_id,
        sender_role=sender_role,
        text=text_clean,
        photo_s3_path=photo_path,
        photo_s3_url=photo_url,
    )
    db.add(msg)
    db.flush()
    return msg


def notify_counterpart(
    db: DBSession,
    *,
    submission: HomeworkSubmission,
    recipient_id: int,
    sender_role: str,
) -> Notification:
    """In-app уведомление без deep-линка: `Notification.work_id` про пробник,
    у домашки нет своей FK-колонки — заводить её ради одного уведомления
    избыточно, пока нет второго потребителя. Текст называет, куда идти."""
    if sender_role == ROLE_STUDENT:
        title = "Ученик ответил по домашке"
    else:
        title = "Куратор оставил обратную связь по домашке"
    n = Notification(
        user_id=recipient_id,
        title=title,
        text=f"По домашней работе #{submission.id} есть новое сообщение — откройте её в кабинете.",
    )
    db.add(n)
    db.flush()
    invalidate_unread(recipient_id)
    return n


def serialize_messages(
    messages: list[HomeworkFeedbackMessage],
    names: dict[int, str] | None = None,
) -> list[dict]:
    names = names or {}
    return [
        {
            "id": m.id,
            "sender_id": m.sender_id,
            "sender_role": m.sender_role,
            "sender_name": names.get(m.sender_id),
            "sender_role_label": role_label_ru(m.sender_role),
            "text": m.text,
            "photo_s3_url": m.photo_s3_url,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


__all__ = [
    "get_or_create_feedback",
    "send_message",
    "notify_counterpart",
    "serialize_messages",
    "role_from_rank",
    "role_label_ru",
]
