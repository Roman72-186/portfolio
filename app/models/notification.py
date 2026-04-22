from datetime import datetime, timezone

from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("works.id", ondelete="SET NULL"), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        # Ускоряет запрос: ORDER BY is_read ASC, created_at DESC
        Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),
    )
