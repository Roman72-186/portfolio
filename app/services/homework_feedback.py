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


async def _upload_video(
    submission_id: int, filename: str, data: bytes, content_type: str
) -> tuple[str, str] | None:
    """Положить видео в S3 как есть (без сжатия). Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    s3_path = s3_service.s3_path_homework_feedback(submission_id, filename)
    ct = content_type or "video/mp4"

    def _do() -> str | None:
        return s3_service.upload_to_s3(s3_path, data, ct)

    try:
        s3_url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning("homework feedback video upload exception for submission_id=%s: %s", submission_id, exc)
        return None
    if s3_service.is_configured() and not s3_url:
        logger.warning("homework feedback video upload failed for submission_id=%s", submission_id)
        return None
    return s3_path, (s3_url or "")


async def _upload_audio(
    submission_id: int, filename: str, data: bytes, content_type: str
) -> tuple[str, str] | None:
    """Положить голосовое в S3 как есть. Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    s3_path = s3_service.s3_path_homework_feedback(submission_id, filename)
    ct = content_type or "audio/mpeg"

    def _do() -> str | None:
        return s3_service.upload_to_s3(s3_path, data, ct)

    try:
        s3_url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning("homework feedback audio upload exception for submission_id=%s: %s", submission_id, exc)
        return None
    if s3_service.is_configured() and not s3_url:
        logger.warning("homework feedback audio upload failed for submission_id=%s", submission_id)
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
    video: tuple[str, bytes, str] | None = None,
    audio: tuple[str, bytes, str] | None = None,
    video_link: str | None = None,
    video_is_note: bool = False,
) -> HomeworkFeedbackMessage:
    """Создать сообщение в диалоге. Хотя бы одно из (text, photo, video,
    audio, video_link) — по образцу `app/services/feedback.py::send_message`
    (владелец 17.09.2026: у домашки должны быть те же вложения, что у
    эталонного диалога пробника/портфолио).

    `video_link` — уже провалидированная (http/https) ссылка, альтернатива
    загрузке файла, валидация на вызывающей стороне
    (`app/services/utils.py::validate_video_link`).

    Без commit."""
    text_clean = (text or "").strip() or None
    photo_path: str | None = None
    photo_url: str | None = None
    if photo is not None:
        filename, data = photo
        uploaded = await _upload_photo(feedback.submission_id, filename, data)
        if uploaded is not None:
            photo_path, photo_url = uploaded
    video_path: str | None = None
    video_url: str | None = None
    if video is not None:
        vfilename, vdata, vcontent_type = video
        uploaded = await _upload_video(feedback.submission_id, vfilename, vdata, vcontent_type)
        if uploaded is not None:
            video_path, video_url = uploaded
    audio_path: str | None = None
    audio_url: str | None = None
    if audio is not None:
        afilename, adata, acontent_type = audio
        uploaded = await _upload_audio(feedback.submission_id, afilename, adata, acontent_type)
        if uploaded is not None:
            audio_path, audio_url = uploaded
    if (
        text_clean is None and photo_url is None and video_url is None
        and audio_url is None and not video_link
    ):
        raise ValueError("Сообщение должно содержать текст, фото, видео, ссылку на видео или голосовое")

    msg = HomeworkFeedbackMessage(
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
        # Кружок записывает только преподаватель (владелец 25.09.2026), и
        # флаг имеет смысл лишь при загруженном видео — проверка здесь, а не в
        # трёх роутах, чтобы ученик не мог прислать круг в обход формы.
        video_is_note=bool(
            video_is_note and video_url is not None and sender_role != ROLE_STUDENT
        ),
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
    title_override: str | None = None,
    text_override: str | None = None,
) -> Notification:
    """In-app уведомление с deep-линком на отдельное окно чата домашки
    (владелец 10.09.2026: `Notification.homework_submission_id`, чат
    домашки больше не встроен в страницу задания).

    `title_override`/`text_override` — для действий с более конкретным
    смыслом, чем «появилось сообщение» (например, возврат на доработку из
    `send_homework_to_revision`), чтобы не заводить второй канал уведомлений
    ради одной другой формулировки."""
    if title_override is not None:
        title = title_override
    elif sender_role == ROLE_STUDENT:
        title = "Ученик ответил по домашке"
    else:
        title = "По домашней работе появилась обратная связь"
    text = text_override if text_override is not None else (
        f"По домашней работе #{submission.id} есть новое сообщение – открой обратную связь."
    )
    n = Notification(
        user_id=recipient_id,
        title=title,
        text=text,
        homework_submission_id=submission.id,
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
            "video_s3_url": m.video_s3_url,
            "video_url": m.video_url,
            "audio_s3_url": m.audio_s3_url,
            "video_is_note": bool(m.video_is_note),
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
