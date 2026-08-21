from datetime import datetime, timezone

from sqlalchemy import Integer, String, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CuratorReport(Base):
    """Видео-отчёт куратора: ссылка на видео + текст. Уходит уведомлением главным преподавателям/SA."""

    __tablename__ = "curator_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    curator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    video_url: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Первый просмотр отчёта главным преподавателем/SA (для метрики «время до просмотра»)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    viewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_curator_reports_curator_created", "curator_id", "created_at"),
    )
