"""Ссылка пробного набора предобучения — по образцу `GuestExamConfig`
(app/models/guest_exam.py): та же задача «ссылка с одним выключателем».

Одна строка на slug (`apparchi.ru/<slug>`, сейчас только «proba»). `is_active`
решает, работает ли ссылка как вход прямо сейчас; `access_until` — какой срок
доступа получит пришедший по ней **новый** пользователь (см.
app/services/intake_link.py и app/api/auth.py). Пока дата не задана, ссылку
нельзя включить — владелец 11.09.2026, чтобы не закрыть доступ незаданным
числом.

С 14.10.2026 (созвон 13.10, каникулярный набор) ссылок несколько: у каждой
группы каникулярного набора своя, `/join/<slug>`. `program` —
какую программу получит новичок (`/proba` — годовая), `tag_id` — тег группы:
по нему каникулярный цикл открывается ученику. Правила — в
app/services/intake_link.py.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class IntakeLink(Base):
    __tablename__ = "intake_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    access_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    program: Mapped[str] = mapped_column(
        String(20), nullable=False, default="annual", server_default="annual"
    )
    tag_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="SET NULL"), nullable=True
    )
    # Трек каникулярного набора (17.10.2026): ссылка группы с ценой продаётся
    # на платформе — оплата ставит тег трека и срок. Без цены — прежняя
    # бесплатная ссылка группы. Каникулярная ссылка без тега — общая ссылка
    # набора: вход без трека. Правила — app/services/intake_link.py.
    price_kop: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Переключатели школы на треке («потенциал», 17.10.2026): напоминания в
    # Telegram и переписка с преподавателем в блоках, отмеченных «Каникулярным
    # набором» в «Настройке диалога».
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    dialog_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
