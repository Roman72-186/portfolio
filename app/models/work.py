from datetime import datetime, timezone

from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, Numeric, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# work_type values
WORK_TYPE_BEFORE = "before"
WORK_TYPE_AFTER = "after"
WORK_TYPE_MOCK_EXAM = "mock_exam"
WORK_TYPE_RETAKE = "retake"


class Work(Base):
    __tablename__ = "works"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    work_type: Mapped[str] = mapped_column(String(20), nullable=False)  # before | after | mock_exam | retake
    month: Mapped[str] = mapped_column(String(20), nullable=False)      # "январь" … "декабрь"
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    s3_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(50), nullable=True)      # "Рисунок" | "Композиция"
    tariff: Mapped[str | None] = mapped_column(String(50), nullable=True)       # "МАКСИМУМ" | "УВЕРЕННЫЙ" | "Я С ВАМИ"
    score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)   # 0.00–100.00 (curator's score)
    student_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)  # student self-reported score (retake)
    sent_to_retake: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scored_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # curator comment on the work
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | success | failed
    drive_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", nullable=False)  # pending | synced | failed | s3_only
    # Цикл Пробника (план 2026-05-14): cycle_id+is_final+parent_work_id+attempt_number
    cycle_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("exam_cycles.id"), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    parent_work_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("works.id"), nullable=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_works_user_type", "user_id", "work_type"),
        Index("ix_works_user_year_month", "user_id", "year", "month"),
        Index("ix_works_user_status_created", "user_id", "status", "created_at"),
        Index("ix_works_status_created", "status", "created_at"),
        Index("ix_works_type_status", "work_type", "status"),
    )
