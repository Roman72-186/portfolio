"""Ответы ученика на мини-опрос из трёх уточняющих вопросов после видео.

Опрос — не то же самое, что тип блока `survey`/анкета недели (там ещё нет
модели вопросов/вариантов, см. TODO.md). Здесь вопросы задаёт преподаватель
прямо на видео (`LearningVideo.quiz_question_1..3`), ответы — свободный текст,
без экрана проверки: решение владельца 23.08 — в этом заходе только хранить.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class VideoQuizResponse(Base):
    __tablename__ = "video_quiz_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("learning_videos.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    answer_1: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_2: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_3: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        UniqueConstraint("video_id", "user_id", name="uq_video_quiz_response_video_user"),
        Index("ix_video_quiz_responses_video", "video_id"),
    )
