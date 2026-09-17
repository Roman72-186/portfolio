"""Диалог обратной связи по работе, сданной в блоке задания.

Отдельная модель сохраняет границы доменов: пробник привязан к Work и
ExamCycle, старая домашка – к HomeworkSubmission, эта ветка – к
TaskBlockSubmission. Полиморфная переделка двух работающих диалогов ради
нового типа сдачи создала бы лишний риск для существующих данных.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class TaskBlockFeedback(Base):
    __tablename__ = "task_block_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("task_block_submissions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    curator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    messages: Mapped[list["TaskBlockFeedbackMessage"]] = relationship(
        "TaskBlockFeedbackMessage", back_populates="feedback", cascade="all, delete-orphan",
        order_by="TaskBlockFeedbackMessage.created_at, TaskBlockFeedbackMessage.id",
    )


class TaskBlockFeedbackMessage(Base):
    __tablename__ = "task_block_feedback_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feedback_id: Mapped[int] = mapped_column(
        ForeignKey("task_block_feedbacks.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    sender_role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_s3_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    feedback: Mapped["TaskBlockFeedback"] = relationship(
        "TaskBlockFeedback", back_populates="messages"
    )

    __table_args__ = (
        Index(
            "ix_task_block_feedback_messages_feedback_created",
            "feedback_id", "created_at",
        ),
        CheckConstraint(
            "(text IS NOT NULL AND length(text) > 0) "
            "OR (photo_s3_url IS NOT NULL) OR (video_url IS NOT NULL)",
            name="ck_task_block_feedback_messages_content",
        ),
    )
