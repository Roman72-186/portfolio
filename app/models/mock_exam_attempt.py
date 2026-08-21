"""Факт начатого пробника. Создаётся при клике «Начать пробник», закрывается
при успешной сдаче или остаётся до истечения 4 часов (тогда considered aborted).

Хранит снимок билета (title/description/image_url), чтобы при редактировании
или удалении билета контент попытки оставался консистентным для ученика.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Integer, String, DateTime, Boolean, ForeignKey, Index, Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class MockExamAttempt(Base):
    __tablename__ = "mock_exam_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(50), nullable=False)

    # Ticket snapshot (на случай если главный преподаватель отредактировал/удалил билет)
    ticket_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("exam_tickets.id", ondelete="SET NULL"), nullable=True
    )
    ticket_title: Mapped[str] = mapped_column(String(200), nullable=False)
    ticket_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Билет попытки вышел из активного периода (или появился новый билет) до
    # того, как ученик сдал работу. В отличие от completed_at (реальная сдача),
    # expired_at — «протухло само», попытка не должна резюмироваться и не
    # должна получать прогресс-уведомления.
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Флаги отправленных уведомлений о прогрессе
    notif_2h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notif_3h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notif_10min_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_mock_exam_attempts_user_active", "user_id", "subject", "completed_at"),
        Index("ix_mock_exam_attempts_progress", "completed_at", "started_at"),
    )
