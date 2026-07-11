from datetime import datetime, timezone

from sqlalchemy import Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AuditLog(Base):
    """Журнал изменений пользователей: действия staff и смены тарифа учеником."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    # user_delete | user_block | user_unblock | curator_assign | tariff_change
    performed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    details: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_audit_logs_performed_by", "performed_by_id", "created_at"),
        Index("ix_audit_logs_target_user", "target_user_id", "created_at"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )
