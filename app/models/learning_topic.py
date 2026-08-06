"""Тема недели для видеоуроков.

Собственная сущность видеомодуля, намеренно не связанная с ExamAssignment: у
пробников своя механика (предмет, билеты, таймер, период сдачи), и переплетение
уже приводило к тому, что урок мог выпасть ученику как вариант пробника.

Тема только открывается по дате — окна закрытия нет. Прошедшая тема остаётся в
каталоге как учебный архив: ученик может вернуться к материалу третьей недели,
сидя на седьмой.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class LearningTopic(Base):
    __tablename__ = "learning_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Когда тема появляется у учеников. Верхней границы нет — см. докстринг модуля.
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Явный флаг, а не правило «нет тегов и нет назначенных значит всем»: молчаливая
    # раздача платного контента при забытой аудитории — слишком дорогая ошибка.
    assign_to_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
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
        Index("ix_learning_topics_public", "is_published", "opens_at"),
    )


class LearningTopicTag(Base):
    """Кому адресована тема по тегам. Сопоставление строгое, по tag_id."""

    __tablename__ = "learning_topic_tags"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("learning_topics.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        Index("ix_learning_topic_tags_tag", "tag_id"),
    )


class LearningTopicAssignee(Base):
    """Поимённое исключение: тема выдана ученику сверх тегов.

    Для догоняющих и индивидуальных разборов — когда ученик не попадает ни в один
    тег адресации, но материал ему нужен.
    """

    __tablename__ = "learning_topic_assignees"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("learning_topics.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_learning_topic_assignees_user", "user_id"),
    )
