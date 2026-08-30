"""Мини-опрос после сдачи любого пункта дня программы (владелец 30.08.2026):
та же конструкция, что уже была у видео и у Пробника (`video_quiz.py`,
`mock_exam_quiz.py`), но общая на все восемь видов `TrackerTask.kind`, а не
своя под каждый. Видео остаётся на собственных таблицах (`video_quiz.py`) —
там мини-опрос завязан на конкретный `LearningVideo`, который живёт вне
одного дня программы (ролик встречается в календаре многократно), и на
проде уже есть реальные ответы учеников, которые не стоит трогать миграцией
ради унификации. Пробник (`mock_exam_quiz.py`) на этот общий стол переехал —
у него собственных ответов ещё не накопилось.

Вопрос привязан к `task_id` (`TrackerTask.id`) напрямую: один день — один
элемент — один набор вопросов, никакой сущности-посредника между ними не
нужно.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Тот же лимит, что у мини-опроса видео (app/models/video_quiz.py) — общий
# язык конструктора, не повод для отдельной константы.
MAX_QUIZ_QUESTIONS = 20


class TaskQuizQuestion(Base):
    """Один вопрос мини-опроса элемента дня. Порядок — `sort_order`."""

    __tablename__ = "task_quiz_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_task_quiz_questions_order", "task_id", "sort_order"),
    )


class TaskQuizResponse(Base):
    """Заполнение мини-опроса одним учеником по одному элементу дня. Сами
    ответы — в `TaskQuizAnswer`, эта строка только группирует их и держит
    уникальность (task_id, user_id)."""

    __tablename__ = "task_quiz_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False
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
        UniqueConstraint("task_id", "user_id", name="uq_task_quiz_response_task_user"),
        Index("ix_task_quiz_responses_task", "task_id"),
    )


class TaskQuizAnswer(Base):
    """Ответ ученика на один вопрос мини-опроса элемента дня.

    Привязан к `question_id`, не к позиции — правка текста вопроса не рвёт
    уже сохранённые ответы (тот же приём, что у `VideoQuizAnswer`).
    """

    __tablename__ = "task_quiz_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("task_quiz_responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("task_quiz_questions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "response_id", "question_id", name="uq_task_quiz_answer_response_question"
        ),
    )
