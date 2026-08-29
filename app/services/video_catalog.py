"""Queries and state transitions for the local learning-video catalogue."""

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models.learning_video import LearningVideo
from app.services.bunny_stream import get_video, is_bunny_stream_available, normalize_bunny_status
from app.services.video_topics import accessible_topic_ids

# Ранг, с которого сотрудник видит все уроки независимо от тем (preview куратора).
STAFF_PREVIEW_RANK = 2


def list_all_videos(db: Session) -> list[LearningVideo]:
    # Единственный вызывающий — админский список (video_admin_page): он
    # рендерит конструктор вопросов на каждую строку, а без selectinload это
    # был бы отдельный SELECT на видео вместо одного общего.
    return (
        db.query(LearningVideo)
        .options(selectinload(LearningVideo.questions))
        .filter(LearningVideo.deleted_at.is_(None))
        .order_by(LearningVideo.sort_order.asc(), LearningVideo.created_at.desc())
        .all()
    )


def _is_staff_viewer(viewer: dict | None) -> bool:
    return bool(viewer) and viewer.get("role_rank", 0) >= STAFF_PREVIEW_RANK


def is_video_accessible(db: Session, video, viewer: dict) -> bool:
    """Открыт ли конкретный урок этому зрителю.

    Урок без темы (topic_id IS NULL) открыт всем ученикам — так вели себя
    все ролики до появления тем, и молча закрывать их при выкатке нельзя.
    Привязанный урок открыт вместе со своей темой.
    """
    if _is_staff_viewer(viewer):
        return True
    topic_id = getattr(video, "topic_id", None)
    if topic_id is None:
        return True
    return topic_id in accessible_topic_ids(db, viewer["user_id"])


def list_published_videos(db: Session, *, viewer: dict) -> list[LearningVideo]:
    if not settings.bunny_stream_enabled:
        return []
    videos = (
        db.query(LearningVideo)
        .filter(
            LearningVideo.deleted_at.is_(None),
            LearningVideo.is_published.is_(True),
            LearningVideo.status == "ready",
        )
        .order_by(LearningVideo.sort_order.asc(), LearningVideo.created_at.asc())
        .all()
    )
    if videos:
        if _is_staff_viewer(viewer):
            return videos
        # Один расчёт доступных тем на весь каталог, а не по ролику.
        allowed = accessible_topic_ids(db, viewer["user_id"])
        return [
            video
            for video in videos
            if video.topic_id is None or video.topic_id in allowed
        ]
    if db.query(LearningVideo.id).first() or not is_bunny_stream_available():
        return []
    return [legacy_pilot_video()]


def legacy_pilot_video():
    """Temporary compatibility object until the deterministic pilot migration runs."""
    return SimpleNamespace(
        id=0,
        bunny_library_id=settings.bunny_stream_library_id,
        bunny_video_id=settings.bunny_stream_video_id,
        title=settings.bunny_stream_video_title,
        description=None,
        status="ready",
        is_published=True,
        duration_seconds=None,
        sort_order=0,
        deleted_at=None,
        topic_id=None,
    )


def get_published_video(db: Session, video_id: int, *, viewer: dict):
    """Опубликованный урок, если он открыт этому зрителю.

    Отдельный от каталога путь: прямой заход по /cabinet/videos/{id} на чужую
    тему обязан упереться в ту же проверку, иначе ссылка обходит фильтр.
    """
    if not settings.bunny_stream_enabled:
        return None
    if video_id == 0 and not db.query(LearningVideo.id).first() and is_bunny_stream_available():
        return legacy_pilot_video()
    video = (
        db.query(LearningVideo)
        .filter(
            LearningVideo.id == video_id,
            LearningVideo.deleted_at.is_(None),
            LearningVideo.is_published.is_(True),
            LearningVideo.status == "ready",
        )
        .first()
    )
    if video is None or not is_video_accessible(db, video, viewer):
        return None
    return video


def publish_video(video: LearningVideo, *, user_id: int) -> None:
    if video.status != "ready" or video.deleted_at is not None:
        raise ValueError("Video is not ready for publication")
    video.is_published = True
    video.published_at = datetime.now(timezone.utc)
    video.published_by_id = user_id


def unpublish_video(video: LearningVideo) -> None:
    video.is_published = False
    video.published_at = None
    video.published_by_id = None


def sync_status_from_bunny(db: Session, video: LearningVideo) -> bool:
    """Подтянуть статус видео с Bunny и опубликовать, если куратор уже попросил
    (`auto_publish_on_ready`) и оно только что стало готовым.

    Общая логика для ручного «Обновить статус» (video_admin.py::refresh_video_status)
    и фоновой проверки (exam_scheduler.py::_run_video_status_sync). Раньше эта
    логика жила только внутри HTTP-обработчика и не запускалась, пока куратор
    не открыл страницу «Загрузка видео» — на часовом ролике куратор успевал
    закрыть вкладку до конца обработки, и статус зависал на `processing`
    навсегда (живой баг, найден 29.08.2026).

    Не коммитит и не ловит BunnyStreamAPIError/BunnyStreamConfigError — это
    обязанность вызывающего (у HTTP-обработчика и джобы разная реакция на сбой).
    Возвращает True, если видео только что стало опубликованным.
    """
    remote = get_video(video.bunny_video_id)
    bunny_status = remote.get("status")
    video.bunny_status = bunny_status if isinstance(bunny_status, int) else None
    was_ready = video.status == "ready"
    video.status = normalize_bunny_status(video.bunny_status)

    encode_progress = remote.get("encodeProgress")
    try:
        video.encode_progress = (
            max(0, min(100, int(encode_progress)))
            if isinstance(encode_progress, (int, float)) else None
        )
    except (ValueError, OverflowError):
        video.encode_progress = None

    duration = remote.get("length")
    try:
        video.duration_seconds = (
            float(duration) if isinstance(duration, (int, float)) and duration >= 0 else None
        )
    except (ValueError, OverflowError):
        video.duration_seconds = None

    messages = remote.get("transcodingMessages")
    last_message = messages[-1] if isinstance(messages, list) and messages else None
    video.status_message = (
        str(last_message.get("message", ""))[:500] or None
        if isinstance(last_message, dict) else None
    )

    just_became_ready = video.status == "ready" and not was_ready
    if just_became_ready and video.auto_publish_on_ready and not video.is_published:
        publish_video(video, user_id=video.created_by_id)
        return True
    return False
