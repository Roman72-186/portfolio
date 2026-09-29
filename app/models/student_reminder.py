"""Отметка «это напоминание ученику уже отправлено» (владелец 29.09.2026).

Уведомления о новом задании, новом видео, сроке сдачи и конце доступа шлёт
планировщик по расписанию (`app/services/student_reminders.py`), а не
действие человека, поэтому одно и то же событие он видит в нескольких
прогонах подряд. Строка здесь и есть «уже слали»: уникальность по
(ученик, вид, ключ) не даёт второму прогону повторить сообщение.

Отдельная таблица, а не флаг на задании или ученике: событий у одного
ученика много (каждое задание, каждый срок, каждый срок доступа), и у
срока ключ несёт само время — продлили срок, ключ другой, напоминание
придёт заново.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

KIND_NEW_TASK = "new_task"
KIND_NEW_VIDEO = "new_video"
KIND_DEADLINE_24H = "deadline_24h"
KIND_DEADLINE_3H = "deadline_3h"
KIND_ACCESS_3D = "access_3d"
KIND_ACCESS_1D = "access_1d"


class StudentReminder(Base):
    __tablename__ = "student_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # id задания или блока, у срока — ещё и сам момент срока.
    ref: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("user_id", "kind", "ref", name="uq_student_reminders_user_kind_ref"),
    )
