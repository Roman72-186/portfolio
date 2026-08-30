"""Мини-опрос после сдачи Пробника — та же конструкция, что у видео
(решение владельца 30.08.2026, см. `app/models/video_quiz.py`): произвольное
число вопросов свободным текстом через конструктор «плюс», без проверки
ответов. Отличие от видео — родитель не `LearningVideo`, а `ExamAssignment`:
билетов у задания может быть несколько (ученику выдаётся случайный), но
вопросы одни на всё задание, не на конкретный билет.

Вопросы задаёт преподаватель при создании Пробника в дне программы
(`app/api/cabinet_program.py::create_mock_item`) — одним списком сразу на все
выбранные предметы, каждый предмет получает свою копию строк (у Рисунка и
Композиции разные `ExamAssignment`). Ответ показывается ученику после
сдачи финального фото — гейт на сервере, как у видео: клиенту верить нельзя.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Тот же лимит, что у мини-опроса видео (app/models/video_quiz.py) — общий
# язык конструктора, не повод для отдельной константы.
MAX_QUIZ_QUESTIONS = 20


class ExamAssignmentQuestion(Base):
    """Один вопрос мини-опроса Пробника. Порядок — `sort_order`."""

    __tablename__ = "exam_assignment_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("exam_assignments.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_exam_assignment_questions_order", "assignment_id", "sort_order"),
    )


class MockQuizResponse(Base):
    """Заполнение мини-опроса одним учеником по одному заданию Пробника.
    Сами ответы — в `MockQuizAnswer`, эта строка только группирует их и
    держит уникальность (assignment_id, user_id)."""

    __tablename__ = "mock_quiz_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("exam_assignments.id", ondelete="CASCADE"), nullable=False
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
        UniqueConstraint("assignment_id", "user_id", name="uq_mock_quiz_response_assignment_user"),
        Index("ix_mock_quiz_responses_assignment", "assignment_id"),
    )


class MockQuizAnswer(Base):
    """Ответ ученика на один вопрос мини-опроса Пробника.

    Привязан к `question_id`, не к позиции — правка текста вопроса не рвёт
    уже сохранённые ответы (тот же приём, что у `VideoQuizAnswer`).
    """

    __tablename__ = "mock_quiz_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("mock_quiz_responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("exam_assignment_questions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "response_id", "question_id", name="uq_mock_quiz_answer_response_question"
        ),
    )
