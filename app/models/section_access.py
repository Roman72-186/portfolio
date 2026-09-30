"""Переключатели доступа к разделам кабинета (владелец 30.09.2026).

Суперадмин закрывает сотрудникам разделы: всей роли сразу или одному
человеку. Переключатель только сужает — потолок по-прежнему задаёт ранг
роли (`require_*` в `app/dependencies.py`), открыть сверх него нельзя.

Строка — либо правило роли (`role_id`), либо личное правило сотрудника
(`user_id`), ровно одно из двух. У роли хранится только «закрыто»: нет
строки — раздел открыт. У сотрудника строка бывает и «открыто» (исключение
из закрытия ролью), и «закрыто»; нет строки — действует правило роли.

Каталог разделов и сама проверка — `app/services/section_access.py`.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class SectionAccessRule(Base):
    __tablename__ = "section_access_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    section_key: Mapped[str] = mapped_column(String(40), nullable=False)
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "(role_id IS NULL) <> (user_id IS NULL)",
            name="ck_section_access_rules_one_target",
        ),
        UniqueConstraint("section_key", "role_id", name="uq_section_access_rules_role"),
        UniqueConstraint("section_key", "user_id", name="uq_section_access_rules_user"),
    )
