from datetime import datetime, date, timezone

from sqlalchemy import Integer, String, Date, DateTime, ForeignKey, Index, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ExamCycle(Base):
    __tablename__ = "exam_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(50), nullable=False)  # Рисунок | Композиция
    ticket_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("exam_tickets.id"), nullable=True)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Оценка для отработки из обратной связи. В отличие от Work.score,
    # не участвует в закрытии цикла и не попадает в итоговую статистику пробников.
    intermediate_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    # SA вернул закрытый цикл автору ОС на правку сообщения. Цикл остаётся закрытым
    # (балл/портфолио/блокировка не трогаются); флаг даёт куратору доступ к правке
    # своих сообщений и подсвечивает цикл в его списке. «Завершить правку» ставит
    # revision_done_at (requested_at сохраняется для статистики времени правки);
    # актуальное состояние «на правке» — см. is_on_revision.
    revision_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revision_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    @property
    def is_on_revision(self) -> bool:
        """Цикл сейчас «на правке»: SA вернул, куратор ещё не завершил."""
        return self.revision_requested_at is not None and self.revision_done_at is None

    __table_args__ = (
        Index("ix_exam_cycles_user_subject_started", "user_id", "subject", text("started_at DESC")),
    )
