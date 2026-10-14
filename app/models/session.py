import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    impersonated_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    # Откуда вошли (14.10.2026, каникулярный набор): школа хочет видеть, не
    # делят ли доступ на несколько человек. Пишет `auth._create_session_response`,
    # адрес — `request.client.host`, за Traefik это настоящий адрес
    # (`--forwarded-allow-ips` в docker-compose.prod-ru.yml).
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)

    @property
    def slides(self) -> bool:
        """Продлевается ли сессия, пока человек работает.

        Вход «глазами» — нет: его 30 минут (`cabinet_superadmin.
        _IMPERSONATION_TTL_MIN`) — предел, а не срок простоя. До 13.10.2026
        скользящее продление считало половину от `SESSION_TTL_HOURS`, и первый же
        запрос растягивал 30 минут до суток (код-ревью 12.10.2026, P2 № 3).
        Читают оба места продления: `dependencies.get_current_user` и
        `main.force_mock_exam_session_refresh`.
        """
        return self.impersonated_by_id is None
