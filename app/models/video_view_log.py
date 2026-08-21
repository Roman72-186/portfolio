"""Лог открытий видео — «когда возвращался», не только «сколько раз».

Отдельно от `VideoProgress` намеренно: та таблица — текущая позиция
просмотра (одна строка на пару ученик×видео), эта — insert-only история
открытий плеера. Смешивать значило бы примешивать счётчик посещений к
семантике «текущая позиция», хотя это разные вопросы к данным.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class VideoViewLog(Base):
    __tablename__ = "video_view_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Тот же bunny_video_id, что у VideoProgress, а не FK на learning_videos.id:
    # легаси-пилотный ролик открывается без строки в каталоге вовсе.
    video_id: Mapped[str] = mapped_column(String(36), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_video_view_logs_user_video", "user_id", "video_id"),
    )
