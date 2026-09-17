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


async def _upload_video(
    submission_id: int, filename: str, data: bytes, content_type: str,
) -> tuple[str, str] | None:
    """Положить видео в S3 как есть (без сжатия). Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    path = s3_service.s3_path_task_block_feedback(submission_id, filename)
    ct = content_type or "video/mp4"

    def _do() -> str | None:
        return s3_service.upload_to_s3(path, data, ct)

    try:
        url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning(
            "task block feedback video upload failed for submission_id=%s: %s",
            submission_id, exc,
        )
        raise ValueError("Не удалось загрузить видео. Попробуй ещё раз.") from exc
    if not url:
        raise ValueError("Не удалось загрузить видео. Попробуй ещё раз.")
    return path, url


async def _upload_audio(
    submission_id: int, filename: str, data: bytes, content_type: str,
) -> tuple[str, str] | None:
    """Положить голосовое в S3 как есть. Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    path = s3_service.s3_path_task_block_feedback(submission_id, filename)
    ct = content_type or "audio/mpeg"

    def _do() -> str | None:
        return s3_service.upload_to_s3(path, data, ct)

    try:
        url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning(
            "task block feedback audio upload failed for submission_id=%s: %s",
            submission_id, exc,
        )
        raise ValueError("Не удалось загрузить голосовое. Попробуй ещё раз.") from exc
    if not url:
        raise ValueError("Не удалось загрузить голосовое. Попробуй ещё раз.")
    return path, url


async def send_message(
    db: DBSession, *, feedback: TaskBlockFeedback, sender_id: int, sender_role: str,
    text: str | None, photo: tuple[str, bytes] | None,
    video: tuple[str, bytes, str] | None = None,
    audio: tuple[str, bytes, str] | None = None,
    video_link: str | None = None,
) -> TaskBlockFeedbackMessage:
    """По образцу `app/services/feedback.py::send_message` (владелец
    17.09.2026: у диалога по блокам задания должны быть те же вложения,
    что у эталонного диалога пробника/портфолио)."""
    text_clean = (text or "").strip() or None
    photo_path = None
    photo_url = None
    if photo is not None:
        uploaded = await _upload_photo(feedback.submission_id, photo[0], photo[1])
        if uploaded is not None:
            photo_path, photo_url = uploaded
    video_path = None
    video_url = None
    if video is not None:
        uploaded = await _upload_video(feedback.submission_id, video[0], video[1], video[2])
        if uploaded is not None:
            video_path, video_url = uploaded
    audio_path = None
    audio_url = None
    if audio is not None:
        uploaded = await _upload_audio(feedback.submission_id, audio[0], audio[1], audio[2])
        if uploaded is not None:
            audio_path, audio_url = uploaded
    if (
        text_clean is None and photo_url is None and video_url is None
        and audio_url is None and not video_link
    ):
        raise ValueError("Сообщение должно содержать текст, фото, видео, ссылку на видео или голосовое")
    message = TaskBlockFeedbackMessage(
        feedback_id=feedback.id,
        sender_id=sender_id,
        sender_role=sender_role,
        text=text_clean,
        photo_s3_path=photo_path,
        photo_s3_url=photo_url,
        video_s3_path=video_path,
        video_s3_url=video_url,
        audio_s3_path=audio_path,
        audio_s3_url=audio_url,
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
            "video_s3_url": message.video_s3_url,
            "video_url": message.video_url,
            "audio_s3_url": message.audio_s3_url,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        }
        for message in messages
    ]


__all__ = [
    "get_or_create_feedback", "notify_counterpart", "role_from_rank",
    "send_message", "serialize_messages",
]
