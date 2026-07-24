from datetime import datetime, timezone

from sqlalchemy import Integer, String, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class LegacyPortfolioPhoto(Base):
    """Историческое фото ученика, импортированное один раз из архива
    Telegram-чат-бота (Leadteh) задним числом. Намеренно не связано с
    системой Work/циклов пробников — read-only витрина по месяцам, без
    locks/notifications/feedback."""

    __tablename__ = "legacy_portfolio_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    dialog_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    month: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_path: Mapped[str] = mapped_column(String(400), nullable=False)
    s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_legacy_portfolio_user_year_month", "user_id", "year", "month"),
        UniqueConstraint("s3_path", name="uq_legacy_portfolio_s3_path"),
    )
