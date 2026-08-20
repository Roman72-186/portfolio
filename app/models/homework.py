"""Домашнее задание — отдельная сущность.

Решение владельца от 20.08 (вопрос Р2 в TODO §0): домашка **не** становится ещё
одним `kind` у `ExamAssignment`. У пробника своя механика — билеты, таймер,
период сдачи, случайная выдача варианта, — и переплетение уже приводило к тому,
что урок мог выпасть ученику как вариант пробника (`AGENTS.md`, Open tasks).

Домашка проще: текст, референсные картинки, и позже — сдача работы учеником и
разбор от куратора. В неделю программы она попадает элементом
`TrackerTask(kind="homework", source_kind="homework", source_id=<id>)`.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class HomeworkAssignment(Base):
    """Само задание: что нужно сделать и на что смотреть."""

    __tablename__ = "homework_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(50), nullable=True)  # «Рисунок» | «Композиция» | None

    # Ждём ли от ученика загрузку работы. Выключено — задание «прочитать и
    # сделать», ученик закрывает его галочкой сам.
    submission_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Сколько файлов ждём. Ноль — ограничения нет.
    max_files: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

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
        Index("ix_homework_assignments_alive", "deleted_at"),
    )


class HomeworkImage(Base):
    """Референсная картинка задания. Их несколько: «задание — картинки плюс
    описание» (TODO §0.1). Файл лежит в S3, как фото билетов пробника."""

    __tablename__ = "homework_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    homework_id: Mapped[int] = mapped_column(
        ForeignKey("homework_assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    image_s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_homework_images_order", "homework_id", "sort_order"),
    )
