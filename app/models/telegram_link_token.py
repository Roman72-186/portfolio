import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TelegramLinkToken(Base):
    """Приглашение для действующего ученика привязать Telegram к его текущему
    аккаунту вместо создания нового при переходе с VK-входа на бота.

    Выдаётся персоналом (см. cabinet_superadmin.py), потребляется ботом при
    ``/start <token>``. В отличие от LoginToken сам по себе сессию не создаёт —
    только связывает User.telegram_chat_id с уже существующим user_id, сохраняя
    портфолио и оценки на месте.
    """

    __tablename__ = "telegram_link_tokens"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    issued_by: Mapped[str] = mapped_column(String(50), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
