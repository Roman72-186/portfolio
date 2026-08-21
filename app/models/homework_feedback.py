"""Диалог обратной связи по домашке — по образцу `Feedback`/`FeedbackMessage`.

Не расширение `Feedback`: та модель жёстко привязана к `Work` (UNIQUE
work_id NOT NULL), а `app/services/feedback.py` в 15+ местах читает
`work_id`/`Work` напрямую в S3-путях и уведомлениях — механика цикла
пробника (revision, закрытие, попытки), которой у домашки нет и не будет.
Полиморфная переделка `Feedback` рисковала бы уже проверенным потоком
пробника ради нового модуля — решение задокументировано в
plans/2026-08-21-apparchi-student-day-detail.md.

Без legacy-полей (greeting/strengths/...) — это формат старой разовой формы
куратора у пробника, у домашки такой истории нет.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class HomeworkFeedback(Base):
    """Контейнер диалога: один на сдачу (UNIQUE submission_id)."""

    __tablename__ = "homework_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("homework_submissions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    curator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    messages: Mapped[list["HomeworkFeedbackMessage"]] = relationship(
        "HomeworkFeedbackMessage",
        back_populates="feedback",
        cascade="all, delete-orphan",
        order_by="HomeworkFeedbackMessage.created_at, HomeworkFeedbackMessage.id",
    )


class HomeworkFeedbackMessage(Base):
    """Сообщение в диалоге: текст, фото ИЛИ видео — хотя бы одно."""

    __tablename__ = "homework_feedback_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feedback_id: Mapped[int] = mapped_column(
        ForeignKey("homework_feedbacks.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    sender_role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_s3_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_s3_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    feedback: Mapped["HomeworkFeedback"] = relationship(
        "HomeworkFeedback", back_populates="messages"
    )

    __table_args__ = (
        Index(
            "ix_homework_feedback_messages_feedback_created", "feedback_id", "created_at"
        ),
        CheckConstraint(
            "(text IS NOT NULL AND length(text) > 0) "
            "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL)",
            name="ck_homework_feedback_messages_text_or_photo",
        ),
    )
