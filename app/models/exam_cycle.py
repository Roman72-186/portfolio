from datetime import datetime, date, timezone

from sqlalchemy import Integer, String, Date, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ExamCycle(Base):
    __tablename__ = "exam_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(50), nullable=False)  # Рисунок | Композиция
    ticket_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("exam_tickets.id"), nullable=True)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        Index("ix_exam_cycles_user_subject_started", "user_id", "subject", "started_at"),
    )
