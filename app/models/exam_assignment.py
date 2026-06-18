from datetime import datetime, date, timezone

from sqlalchemy import (
    Integer, String, Boolean, DateTime, Date,
    ForeignKey, Text, Index, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ExamAssignment(Base):
    """Экзаменационное задание — набор из 1–5 билетов по одному предмету."""

    __tablename__ = "exam_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(50), nullable=False)    # "Рисунок" | "Композиция"
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # draft | published | archived
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_exam_assignments_status", "status"),
    )


class ExamTicket(Base):
    """Отдельный билет внутри задания: фото + описание + период + назначение."""

    __tablename__ = "exam_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_number: Mapped[int] = mapped_column(Integer, nullable=False)    # 1 … 10
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)   # plain text / markdown
    image_s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Если True — получить билет нельзя позже (closes_at - duration_minutes), т.е.
    # когда до конца периода остаётся меньше «времени на выполнение». Если False —
    # получить билет можно до самого closes_at, время на выполнение при этом всё
    # равно показывается ученику (визуальный счётчик), см. mock_exam_access.py.
    restrict_start_by_duration: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    target_tag_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assign_to_all: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_exam_tickets_assignment", "assignment_id", "ticket_number"),
        Index("ix_exam_tickets_start_date", "start_date"),
        Index("ix_exam_tickets_time_window", "opens_at", "closes_at"),
    )


class ExamTicketAssignee(Base):
    """Ученик, которому выдан конкретный билет."""

    __tablename__ = "exam_ticket_assignees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("ticket_id", "user_id", name="uq_exam_ticket_user"),
        Index("ix_exam_ticket_assignees_user", "user_id"),
    )
