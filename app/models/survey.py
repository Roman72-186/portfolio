"""Модель анкеты: конструктор вопросов преподавателя + ответы ученика.

Черновой конструктор (владелец сам подчёркивает — не финальное ТЗ, TODO.md §13):
название → список вопросов, у каждого вопроса свой тип (один вариант ответа,
несколько вариантов ответа, свободный текст), преподаватель помечает верный(-е)
вариант(ы) — смысл этого поля для анкет эмоционального состояния не определён,
но поле нужно для учебных анкет-викторин. Ответы ученика просто хранятся,
разбор/сегментация (в т.ч. по эмоциональному состоянию) — отдельная ИИ-стройка
позже, в этой модели её нет.

Анкета — переиспользуемый шаблон, а не разовый контент: карта продукта
(`АНАЛИЗ_ТРЕБОВАНИЙ_ПЛАТФОРМЫ.md` §7.2) описывает анкету эмоционального
состояния на восемь разных точек года — один и тот же набор вопросов
показывается ученику несколько раз за год. Поэтому у ответа уникальность по
(`task_id`, `user_id`), не по (`survey_id`, `user_id`): анкета — это
TrackerTask(kind="survey", source_kind="survey", source_id=Survey.id) на
каждое появление в неделе, и повторная раздача той же анкеты должна снова быть
доступна для заполнения, а не блокироваться прошлым ответом.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


QUESTION_SINGLE = "single"      # один вариант ответа, один верный (викторина)
QUESTION_MULTIPLE = "multiple"  # несколько вариантов ответа, два и более верных
QUESTION_TEXT = "text"          # свободный текст, без вариантов

QUESTION_TYPES = (QUESTION_SINGLE, QUESTION_MULTIPLE, QUESTION_TEXT)

QUESTION_TYPE_LABELS = {
    QUESTION_SINGLE: "Один вариант ответа",
    QUESTION_MULTIPLE: "Несколько вариантов ответа",
    QUESTION_TEXT: "Свободный текст",
}


class Survey(Base):
    """Сама анкета: заголовок и владелец конструктора."""

    __tablename__ = "surveys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        Index("ix_surveys_alive", "deleted_at"),
    )


class SurveyQuestion(Base):
    """Один вопрос анкеты, с типом ответа. Порядок — как преподаватель
    расставил в конструкторе."""

    __tablename__ = "survey_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False, default=QUESTION_TEXT)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_survey_questions_order", "survey_id", "sort_order"),
    )


class SurveyOption(Base):
    """Вариант ответа — только у вопросов типа single/multiple."""

    __tablename__ = "survey_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    # Смысл поля для анкет эмоционального состояния не определён владельцем
    # (TODO.md §13) — хранится ради учебных анкет-викторин, разбор откладывается.
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_survey_options_order", "question_id", "sort_order"),
    )


class SurveyResponse(Base):
    """Заполнение анкеты одним учеником по одному её появлению в неделе
    (см. докстроку модуля про уникальность по task_id, а не survey_id)."""

    __tablename__ = "survey_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_survey_response_task_user"),
        Index("ix_survey_responses_survey", "survey_id"),
    )


class SurveyAnswer(Base):
    """Ответ ученика на один вопрос анкеты. Свободный текст — в `text`,
    выбранные варианты single/multiple — через `SurveyAnswerOption`."""

    __tablename__ = "survey_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("survey_responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("response_id", "question_id", name="uq_survey_answer_response_question"),
    )


class SurveyAnswerOption(Base):
    """Выбранный вариант в ответе — одна строка на single-choice, несколько
    строк на multiple-choice."""

    __tablename__ = "survey_answer_options"

    answer_id: Mapped[int] = mapped_column(
        ForeignKey("survey_answers.id", ondelete="CASCADE"), primary_key=True
    )
    option_id: Mapped[int] = mapped_column(
        ForeignKey("survey_options.id", ondelete="CASCADE"), primary_key=True
    )
