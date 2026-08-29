"""Мини-опрос из уточняющих вопросов после видео.

Решение владельца 22.08: после просмотра видео показать мини-опрос
(plans/2026-08-22-…, п.8.1) из свободных текстовых вопросов, ответы не
проверяются. Изначально вопросов было ровно три — задавались тремя
колонками `LearningVideo.quiz_question_1..3`, а ответы — тремя колонками
`VideoQuizResponse.answer_1..3`. 29.08.2026 владелец попросил неограниченное
число вопросов через конструктор «плюс — новая строка» (тот же паттерн, что у
конструктора анкеты, `app/models/survey.py`) — модель нормализована по тому
же образцу: вопросы и ответы вынесены в отдельные таблицы, каждый вопрос
адресуется своим `id`.

Вопросы задаёт преподаватель в админке видео (`app/api/video_admin.py`,
`app/services/video_quiz.py::sync_questions`), ответы — свободный текст, без
экрана проверки: решение владельца 23.08 — в этом заходе только хранить.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# Верхняя граница числа вопросов — общая для конструктора преподавателя и
# формы ответа ученика (payload-модели в app/api/video_admin.py и
# app/api/video.py). Один источник правды: если предел разъедется,
# преподаватель сможет сохранить больше вопросов, чем разрешает форма
# ответа, и ученик будет получать 422 без возможности исправить это
# самостоятельно.
MAX_QUIZ_QUESTIONS = 20


class LearningVideoQuestion(Base):
    """Один вопрос мини-опроса. Порядок — как преподаватель расставил в
    конструкторе (`sort_order`), не порядок добавления строк."""

    __tablename__ = "learning_video_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("learning_videos.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    video = relationship("LearningVideo", back_populates="questions")

    __table_args__ = (
        Index("ix_learning_video_questions_order", "video_id", "sort_order"),
    )


class VideoQuizResponse(Base):
    """Заполнение мини-опроса одним учеником по одному видео. Сами ответы на
    отдельные вопросы — в `VideoQuizAnswer`; эта строка только группирует их
    и держит уникальность (video_id, user_id)."""

    __tablename__ = "video_quiz_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("learning_videos.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
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
        UniqueConstraint("video_id", "user_id", name="uq_video_quiz_response_video_user"),
        Index("ix_video_quiz_responses_video", "video_id"),
    )


class VideoQuizAnswer(Base):
    """Ответ ученика на один вопрос мини-опроса.

    Ответ привязан к `question_id`, не к позиции: правка текста вопроса
    преподавателем не трогает уже сохранённые ответы. Удаление вопроса
    (`app/services/video_quiz.py::sync_questions`) удаляет и ответы на него —
    сервисный слой делает это явно, не полагаясь на `ON DELETE CASCADE`,
    потому что тесты гоняются на SQLite, а он не исполняет внешние ключи по
    умолчанию.
    """

    __tablename__ = "video_quiz_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("video_quiz_responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("learning_video_questions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "response_id", "question_id", name="uq_video_quiz_answer_response_question"
        ),
    )
