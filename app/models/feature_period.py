from datetime import datetime, date, timezone

from sqlalchemy import Integer, String, Boolean, DateTime, Date, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class FeaturePeriod(Base):
    """Период доступности раздела для студентов. Создаётся главным преподавателем/суперадмином."""

    __tablename__ = "feature_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(30), nullable=False)
    # portfolio_upload | mock_exam | retake
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_feature_periods_feature_active", "feature", "is_active"),
        Index("ix_feature_periods_dates", "start_date", "end_date"),
    )
