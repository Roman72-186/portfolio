"""Read and atomically upsert per-user video playback progress."""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.models.video_progress import VideoProgress


def get_video_progress(
    db: DBSession,
    *,
    user_id: int,
    video_id: str,
) -> VideoProgress | None:
    return db.get(VideoProgress, (user_id, video_id))


def get_resume_position(progress: VideoProgress | None) -> float:
    if progress is None or progress.position_seconds < 5:
        return 0.0
    if (
        progress.duration_seconds is not None
        and progress.duration_seconds - progress.position_seconds <= 10
    ):
        return 0.0
    return round(progress.position_seconds, 1)


def save_video_progress(
    db: DBSession,
    *,
    user_id: int,
    video_id: str,
    position_seconds: float,
    duration_seconds: float | None,
    completed: bool,
) -> bool:
    """Persist the latest position and preserve the first completion timestamp."""
    now = datetime.now(timezone.utc)
    completed_at = now if completed else None
    insert_values = {
        "user_id": user_id,
        "video_id": video_id,
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "completed_at": completed_at,
        "created_at": now,
        "updated_at": now,
    }
    update_values = {
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "completed_at": func.coalesce(VideoProgress.completed_at, completed_at),
        "updated_at": now,
    }

    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(VideoProgress).values(**insert_values)
        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "video_id"],
            set_=update_values,
        )
        db.execute(statement)
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        statement = insert(VideoProgress).values(**insert_values)
        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "video_id"],
            set_=update_values,
        )
        db.execute(statement)
    else:
        progress = get_video_progress(db, user_id=user_id, video_id=video_id)
        if progress is None:
            db.add(VideoProgress(**insert_values))
        else:
            progress.position_seconds = position_seconds
            progress.duration_seconds = duration_seconds
            progress.updated_at = now
            if completed and progress.completed_at is None:
                progress.completed_at = now

    db.commit()
    db.expire_all()
    return completed
