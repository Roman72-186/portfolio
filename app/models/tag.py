from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, DateTime, ForeignKey, Index, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Скрыт из показа: выпадающие списки, подсказки, чипы учеников. Привязки и
    # доступ по тегу не меняются. Июньские теги прошлого потока скрыты
    # миграцией f7c2a9d4b6e1 (владелец 29.09.2026); ручная постановка тега
    # с тем же именем возвращает его в показ (`cabinet_tags.superadmin_add_tag`).
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )


class UserTag(Base):
    __tablename__ = "user_tags"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_user_tags_user", "user_id"),
    )
