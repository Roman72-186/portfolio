from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Feedback(Base):
    """Контейнер диалога обратной связи: 1 на финальную Work (UNIQUE work_id).

    Поля greeting/strengths/weaknesses/recommendations — deprecated (старая
    «разовая форма куратора»). Не пишем в них в новом коде; оставлены для
    обратной совместимости и доступа к историческим данным.
    """

    __tablename__ = "feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("works.id"), nullable=False, unique=True
    )
    curator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    # Deprecated columns (не используются в новом коде, оставлены для legacy данных)
    greeting: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[str | None] = mapped_column(Text, nullable=True)
    weaknesses: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendations: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    messages: Mapped[list["FeedbackMessage"]] = relationship(
        "FeedbackMessage",
        back_populates="feedback",
        cascade="all, delete-orphan",
        order_by="FeedbackMessage.created_at, FeedbackMessage.id",
    )


class FeedbackPhoto(Base):
    """Deprecated: оставлено для legacy данных. Новые фото — в FeedbackMessage."""

    __tablename__ = "feedback_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feedback_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("feedbacks.id", ondelete="CASCADE"), nullable=False
    )
    s3_path: Mapped[str] = mapped_column(String(500), nullable=False)
    s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    order_idx: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        Index("ix_feedback_photos_feedback_id", "feedback_id"),
    )


class FeedbackMessage(Base):
    """Сообщение в диалоге обратной связи. Текст, фото ИЛИ видео (хотя бы одно)."""

    __tablename__ = "feedback_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feedback_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("feedbacks.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    sender_role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_s3_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_s3_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    feedback: Mapped["Feedback"] = relationship("Feedback", back_populates="messages")

    __table_args__ = (
        Index("ix_feedback_messages_feedback_created", "feedback_id", "created_at"),
        CheckConstraint(
            "(text IS NOT NULL AND length(text) > 0) "
            "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL)",
            name="ck_feedback_messages_text_or_photo",
        ),
    )
