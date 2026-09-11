"""Ссылка пробного набора предобучения — по образцу `GuestExamConfig`
(app/models/guest_exam.py): та же задача «ссылка с одним выключателем».

Одна строка на slug (`apparchi.ru/<slug>`, сейчас только «proba»). `is_active`
решает, работает ли ссылка как вход прямо сейчас; `access_until` — какой срок
доступа получит пришедший по ней **новый** пользователь (см.
app/services/intake_link.py и app/api/auth.py). Пока дата не задана, ссылку
нельзя включить — владелец 11.09.2026, чтобы не закрыть доступ незаданным
числом.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class IntakeLink(Base):
    __tablename__ = "intake_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    access_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
