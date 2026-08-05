"""Locally managed catalogue of Bunny Stream learning videos."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class LearningVideo(Base):
    __tablename__ = "learning_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bunny_library_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bunny_video_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    original_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Тема, к которой привязан урок (ExamAssignment). NULL — урок открыт всем
    # ученикам, как было до появления тем; доступ к привязанному уроку считает
    # app/services/video_catalog.py по билетам темы (тег/всем + opens_at).
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_assignments.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="uploading")
    bunny_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    encode_progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_learning_videos_public", "is_published", "status", "sort_order"),
        Index("ix_learning_videos_status", "status"),
        Index("ix_learning_videos_assignment", "assignment_id"),
    )
