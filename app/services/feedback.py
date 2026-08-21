"""Feedback dialog (редизайн 2026-05-23).

Контейнер `Feedback` теперь — заголовок диалога по работе (UNIQUE work_id).
Сообщения хранятся в `FeedbackMessage` (текст ИЛИ фото).

Старые поля Feedback.{greeting,strengths,weaknesses,recommendations} и таблица
`feedback_photos` — deprecated, оставлены для обратной совместимости.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.models.feedback import Feedback, FeedbackMessage
from app.models.notification import Notification
from app.models.work import Work
from app.services import s3 as s3_service
from app.services.utils import compress_image

logger = logging.getLogger(__name__)

MAX_FEEDBACK_PHOTO_INPUT_SIZE = 25 * 1024 * 1024
MAX_FEEDBACK_PHOTO_STORED_SIZE = 10 * 1024 * 1024

# Видео-вложения в диалоге ОС — зеркалят параметры видео-отчёта куратора
# (см. app/api/cabinet_curator.py, который импортирует эти же константы).
MAX_FEEDBACK_VIDEO_SIZE = 500 * 1024 * 1024
ALLOWED_FEEDBACK_VIDEO_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-msvideo",
    "video/x-matroska",
    "video/x-ms-wmv",
    "video/3gpp",
    "video/3gpp2",
}
ALLOWED_FEEDBACK_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".webm",
    ".avi",
    ".mkv",
    ".wmv",
    ".3gp",
    ".3gpp",
    ".m4v",
}

ROLE_STUDENT = "student"
ROLE_CURATOR = "curator"
ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"
STAFF_ROLES = {ROLE_CURATOR, ROLE_ADMIN, ROLE_SUPERADMIN}

ROLE_LABELS_RU = {
    ROLE_STUDENT: "Ученик",
    ROLE_CURATOR: "Куратор",
    ROLE_ADMIN: "Главный преподаватель",
    ROLE_SUPERADMIN: "Суперадмин",
}


def role_label_ru(sender_role: str) -> str:
    """Человекочитаемая русская подпись роли отправителя сообщения ОС."""
    return ROLE_LABELS_RU.get(sender_role, sender_role)


def role_from_rank(role_rank: int) -> str:
    """Map numeric role_rank → sender_role string for FeedbackMessage."""
    if role_rank >= 5:
        return ROLE_SUPERADMIN
    if role_rank >= 4:
        return ROLE_ADMIN
    if role_rank >= 2:
        return ROLE_CURATOR
    return ROLE_STUDENT


def get_or_create_feedback(
    db: DBSession, *, work_id: int, initiator_id: int
) -> tuple[Feedback, bool]:
    """Получить или создать контейнер Feedback. Только staff может инициировать.

    Returns (feedback, created).
    """
    fb = db.query(Feedback).filter(Feedback.work_id == work_id).first()
    if fb is not None:
        return fb, False
    fb = Feedback(work_id=work_id, curator_id=initiator_id)
    db.add(fb)
    db.flush()
    return fb, True


async def _upload_photo(work_id: int, filename: str, data: bytes) -> tuple[str, str] | None:
    """Сжать и положить фото в S3. Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    s3_path = s3_service.s3_path_feedback(work_id, filename)

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
        logger.warning("feedback photo upload exception for work_id=%s: %s", work_id, exc)
        return None
    if s3_service.is_configured() and not s3_url:
        logger.warning("feedback photo upload failed for work_id=%s", work_id)
        return None
    return s3_path, (s3_url or "")


async def _upload_video(
    work_id: int, filename: str, data: bytes, content_type: str
) -> tuple[str, str] | None:
    """Положить видео в S3 как есть (без сжатия). Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    s3_path = s3_service.s3_path_feedback(work_id, filename)
    ct = content_type or "video/mp4"

    def _do() -> str | None:
        return s3_service.upload_to_s3(s3_path, data, ct)

    try:
        s3_url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning("feedback video upload exception for work_id=%s: %s", work_id, exc)
        return None
    if s3_service.is_configured() and not s3_url:
        logger.warning("feedback video upload failed for work_id=%s", work_id)
        return None
    return s3_path, (s3_url or "")


async def send_message(
    db: DBSession,
    *,
    feedback: Feedback,
    sender_id: int,
    sender_role: str,
    text: str | None,
    photo: tuple[str, bytes] | None,
    video: tuple[str, bytes, str] | None = None,
) -> FeedbackMessage:
    """Создать новое сообщение в диалоге. Хотя бы одно из (text, photo, video).

    Не делает commit — caller отвечает за транзакцию.
    """
    text_clean = (text or "").strip() or None
    photo_path: str | None = None
    photo_url: str | None = None
    if photo is not None:
        filename, data = photo
        uploaded = await _upload_photo(feedback.work_id, filename, data)
        if uploaded is not None:
            photo_path, photo_url = uploaded
    video_path: str | None = None
    video_url: str | None = None
    if video is not None:
        vfilename, vdata, vcontent_type = video
        uploaded = await _upload_video(feedback.work_id, vfilename, vdata, vcontent_type)
        if uploaded is not None:
            video_path, video_url = uploaded
    if text_clean is None and photo_url is None and video_url is None:
        raise ValueError("Сообщение должно содержать текст, фото или видео")

    msg = FeedbackMessage(
        feedback_id=feedback.id,
        sender_id=sender_id,
        sender_role=sender_role,
        text=text_clean,
        photo_s3_path=photo_path,
        photo_s3_url=photo_url,
        video_s3_path=video_path,
        video_s3_url=video_url,
    )
    db.add(msg)
    db.flush()
    return msg


def notify_counterpart(
    db: DBSession,
    *,
    work: Work,
    recipient_id: int,
    sender_role: str,
) -> Notification:
    """In-app уведомление получателю о новом сообщении в диалоге."""
    if sender_role == ROLE_STUDENT:
        title = "Ученик ответил в обратной связи"
    else:
        title = "Куратор оставил обратную связь"
    n = Notification(
        user_id=recipient_id,
        title=title,
        text=f"По работе #{work.id} ({work.subject or ''}) есть новое сообщение.",
        work_id=work.id,
    )
    db.add(n)
    db.flush()
    invalidate_unread(recipient_id)
    return n


def serialize_messages(
    messages: list[FeedbackMessage],
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
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]
