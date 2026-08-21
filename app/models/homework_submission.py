"""Сдача домашней работы учеником — симметрично `HomeworkAssignment`/`HomeworkImage`.

Привязана к `tracker_task_id`, не только к `homework_id`: `copy_week()`
(`app/services/tracker.py`) дублирует `HomeworkAssignment` при копировании
недели, и без привязки к конкретной постановке задачи сдача по одной неделе
перепуталась бы со сдачей по её копии.

Отдельно от `Work`/`ExamCycle` (решение владельца, вариант Б, TODO §0 Р2):
у пробника есть билет, таймер и попытка, у домашки — нет ни одного из них, и
переплетение уже один раз роняло доступ к урокам (см. `app/models/homework.py`).
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

STATUS_SUBMITTED = "submitted"
STATUS_NEEDS_REVISION = "needs_revision"
STATUS_ACCEPTED = "accepted"

SUBMISSION_STATUSES = (STATUS_SUBMITTED, STATUS_NEEDS_REVISION, STATUS_ACCEPTED)


class HomeworkSubmission(Base):
    """Одна сдача одним учеником одной постановки задачи.

    Финальное фото перезаписывается in-place при пересдаче (по образцу
    `Work.is_final` у пробника) — история пересдач не нужна, важен только
    актуальный результат и диалог обратной связи вокруг него.
    """

    __tablename__ = "homework_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    homework_id: Mapped[int] = mapped_column(
        ForeignKey("homework_assignments.id", ondelete="CASCADE"), nullable=False
    )
    tracker_task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_SUBMITTED)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
        Index(
            "ix_homework_submissions_task_user",
            "tracker_task_id",
            "user_id",
            unique=True,
        ),
        Index("ix_homework_submissions_user", "user_id"),
    )


class HomeworkSubmissionImage(Base):
    """Фото сдачи: ровно одно финальное + до 10 промежуточных (как у пробника)."""

    __tablename__ = "homework_submission_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("homework_submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    image_s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_homework_submission_images_submission", "submission_id", "sort_order"),
    )
