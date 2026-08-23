"""Feedback dialog (редизайн 2026-05-23).

Контейнер `Feedback` теперь — заголовок диалога по работе (UNIQUE work_id).
Сообщения хранятся в `FeedbackMessage` (текст ИЛИ фото).

Старые поля Feedback.{greeting,strengths,weaknesses,recommendations} и таблица
`feedback_photos` — deprecated, оставлены для обратной совместимости.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.notification import Notification
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
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

# Голосовые сообщения — решение владельца 22.08 («вложения фото/видео/
# голосовое/текст»), закрыто 23.08: типы охватывают и запись из браузера
# (webm/ogg-opus), и голосовые из мессенджеров/диктофона телефона (m4a/aac/
# amr/3gp), которыми ученики и кураторы реально пользуются.
MAX_FEEDBACK_AUDIO_SIZE = 25 * 1024 * 1024
ALLOWED_FEEDBACK_AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/opus",
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/amr",
    "audio/3gpp",
}
ALLOWED_FEEDBACK_AUDIO_EXTENSIONS = {
    ".mp3", ".ogg", ".oga", ".opus", ".webm", ".wav", ".m4a", ".aac", ".amr", ".3gp",
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


async def _upload_audio(
    work_id: int, filename: str, data: bytes, content_type: str
) -> tuple[str, str] | None:
    """Положить голосовое в S3 как есть (без перекодирования). Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    s3_path = s3_service.s3_path_feedback(work_id, filename)
    ct = content_type or "audio/mpeg"

    def _do() -> str | None:
        return s3_service.upload_to_s3(s3_path, data, ct)

    try:
        s3_url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning("feedback audio upload exception for work_id=%s: %s", work_id, exc)
        return None
    if s3_service.is_configured() and not s3_url:
        logger.warning("feedback audio upload failed for work_id=%s", work_id)
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
    audio: tuple[str, bytes, str] | None = None,
) -> FeedbackMessage:
    """Создать новое сообщение в диалоге. Хотя бы одно из (text, photo, video, audio).

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
    audio_path: str | None = None
    audio_url: str | None = None
    if audio is not None:
        afilename, adata, acontent_type = audio
        uploaded = await _upload_audio(feedback.work_id, afilename, adata, acontent_type)
        if uploaded is not None:
            audio_path, audio_url = uploaded
    if text_clean is None and photo_url is None and video_url is None and audio_url is None:
        raise ValueError("Сообщение должно содержать текст, фото, видео или голосовое")

    msg = FeedbackMessage(
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
            "audio_s3_url": m.audio_s3_url,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


def list_student_cycle_cards(
    db: DBSession, user_id: int
) -> tuple[list[dict], list[dict]]:
    """Циклы Пробника ученика с непрочитанным по финалкам — открытые и закрытые.

    Общий движок для полного экрана `/cabinet/cycle` (`cabinet_cycle_hub`) и
    вкладки «Обратная связь» на «Актуальном образовательном пространстве»
    (`/cabinet/learning`, решение владельца 22.08 — вкладка переиспользует код
    цикла пробника, новых моделей не заводит). Оба экрана показывают один и тот
    же список циклов, вкладка АОП просто урезает его до открытых.
    """
    cycles_q = (
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user_id)
        .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
        .all()
    )

    open_cycles: list[dict] = []
    closed_cycles: list[dict] = []
    if not cycles_q:
        return open_cycles, closed_cycles

    cycle_ids = [c.id for c in cycles_q]
    finals_by_cycle: dict[int, list[Work]] = {}
    for w in (
        db.query(Work)
        .filter(Work.cycle_id.in_(cycle_ids), Work.is_final == True)  # noqa: E712
        .all()
    ):
        finals_by_cycle.setdefault(w.cycle_id, []).append(w)
    all_work_ids = [w.id for ws in finals_by_cycle.values() for w in ws]
    unread_work_ids: set[int] = set()
    if all_work_ids:
        unread_work_ids = {
            row[0] for row in db.query(Notification.work_id).filter(
                Notification.user_id == user_id,
                Notification.work_id.in_(all_work_ids),
                Notification.is_read == False,  # noqa: E712
            ).all()
        }
    for c in cycles_q:
        finals = finals_by_cycle.get(c.id, [])
        scored_finals = [
            w for w in finals
            if w.work_type == WORK_TYPE_MOCK_EXAM and w.score is not None
        ]
        if not scored_finals:
            scored_finals = [w for w in finals if w.score is not None]
        close_score = None
        if scored_finals:
            close_work = max(
                scored_finals,
                key=lambda w: (
                    w.scored_at or w.created_at or datetime.min,
                    w.id or 0,
                ),
            )
            close_score = float(close_work.score)
        item = {
            "id": c.id,
            "subject": c.subject,
            "started_at": c.started_at.isoformat(),
            "closed_at": c.closed_at.isoformat() if c.closed_at else None,
            "close_score": close_score,
            "attempts": len(finals),
            "unread_count": sum(1 for w in finals if w.id in unread_work_ids),
        }
        if c.closed_at is None:
            open_cycles.append(item)
        else:
            closed_cycles.append(item)

    return open_cycles, closed_cycles
