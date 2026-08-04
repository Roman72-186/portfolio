"""Queries and state transitions for the local learning-video catalogue."""

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.config import settings
from app.models.learning_video import LearningVideo
from app.services.bunny_stream import is_bunny_stream_available


def list_all_videos(db: Session) -> list[LearningVideo]:
    return (
        db.query(LearningVideo)
        .filter(LearningVideo.deleted_at.is_(None))
        .order_by(LearningVideo.sort_order.asc(), LearningVideo.created_at.desc())
        .all()
    )


def list_published_videos(db: Session) -> list[LearningVideo]:
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
        return videos
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
    )


def get_published_video(db: Session, video_id: int):
    if not settings.bunny_stream_enabled:
        return None
    if video_id == 0 and not db.query(LearningVideo.id).first() and is_bunny_stream_available():
        return legacy_pilot_video()
    return (
        db.query(LearningVideo)
        .filter(
            LearningVideo.id == video_id,
            LearningVideo.deleted_at.is_(None),
            LearningVideo.is_published.is_(True),
            LearningVideo.status == "ready",
        )
        .first()
    )


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
