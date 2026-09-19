"""Per-user playback progress for protected learning videos."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class VideoProgress(Base):
    __tablename__ = "video_progress"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    video_id: Mapped[str] = mapped_column(String(36), primary_key=True, nullable=False)
    position_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Накопленное реальное (календарное) время просмотра — отдельно от
    # position_seconds, которую можно перемотать одним движением ползунка.
    # Владелец 05.09.2026: защита от перемотки — сверять, что реального
    # времени просмотра набралось примерно на длину ролика, а не просто
    # что плеер сообщил позицию у конца. См. app/services/video_progress.py
    # ::compute_watched_seconds.
    watched_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # completed_at keeps the first successful watch for historical reporting;
    # last_completed_at scopes a later rewatch to a newly created task block.
    last_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Lifetime watched_seconds at the latest confirmed completion. A repeated
    # assignment must accumulate a fresh video's worth after this checkpoint.
    last_completion_watched_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
