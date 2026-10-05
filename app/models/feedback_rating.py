"""Оценка учеником обратной связи куратора (ОС, фаза 2).

Созвон 30.09.2026, правила подтверждены владельцем 01.10.2026 (план
`plans/2026-10-01-apparchi-call-30-09-followup.md`, О10–О26): после кнопки
сотрудника «Завершить ОС» ученик ставит 1–5 с обязательным комментарием и
до трёх скриншотов. Одна оценка на диалог, после отправки не меняется.

Диалогов ОС два вида — сдача в блоке задания (`TaskBlockFeedback`) и пробник
(`Feedback`), поэтому ссылка полиморфная: пара (`dialog_kind`, `dialog_id`)
без внешнего ключа, уникальная. Задание и вид ОС записываются в момент
оценки: средняя куратора считается «по каждому заданию цикла» (О19), и
переименование или удаление блока не должно перекладывать старые оценки.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# Какой диалог оценён — какая таблица стоит за `dialog_id`.
DIALOG_TASK_BLOCK = "task_block"   # task_block_feedbacks.id
DIALOG_MOCK_EXAM = "mock_exam"     # feedbacks.id (диалог пробника)
DIALOG_KINDS = (DIALOG_TASK_BLOCK, DIALOG_MOCK_EXAM)

# Вид ОС для статистики и служебного топика (О16: «берётся из типа блока»).
FEEDBACK_HOMEWORK = "homework"     # «Домашнее задание», «Фото + сдача»
FEEDBACK_CONTROL = "control"       # «Работа на время»
FEEDBACK_MOCK = "mock"             # пробник
FEEDBACK_TYPE_LABELS = {
    FEEDBACK_HOMEWORK: "Домашка",
    FEEDBACK_CONTROL: "Контрольная",
    FEEDBACK_MOCK: "Пробник",
}

RATING_MIN = 1
RATING_MAX = 5
RATING_COMMENT_MAX = 2000
RATING_MAX_SCREENSHOTS = 3


class FeedbackRating(Base):
    __tablename__ = "feedback_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dialog_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    dialog_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Автор ОС — `curator_id` диалога, сотрудник, который его начал; кто нажал
    # «Завершить ОС», не важно (владелец 05.10.2026). Аккаунт
    # куратора не передаётся другому человеку (О20), иначе средняя смешала бы
    # двух людей.
    curator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    feedback_type: Mapped[str] = mapped_column(String(20), nullable=False)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracker_tasks.id", ondelete="SET NULL"), nullable=True
    )
    # Подпись задания на момент оценки: у пробника задания нет, а удалённое
    # задание оставило бы строку статистики без названия.
    task_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    images: Mapped[list["FeedbackRatingImage"]] = relationship(
        "FeedbackRatingImage", cascade="all, delete-orphan",
        order_by="FeedbackRatingImage.sort_order, FeedbackRatingImage.id",
    )

    __table_args__ = (
        UniqueConstraint("dialog_kind", "dialog_id", name="uq_feedback_ratings_dialog"),
        CheckConstraint(
            f"score >= {RATING_MIN} AND score <= {RATING_MAX}",
            name="ck_feedback_ratings_score_range",
        ),
        CheckConstraint("length(comment) > 0", name="ck_feedback_ratings_comment"),
        Index("ix_feedback_ratings_curator", "curator_id"),
    )


class FeedbackRatingImage(Base):
    """Скриншот к комментарию оценки (О13: до трёх)."""

    __tablename__ = "feedback_rating_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rating_id: Mapped[int] = mapped_column(
        ForeignKey("feedback_ratings.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    image_s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    image_s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
