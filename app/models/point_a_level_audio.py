from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PointALevelAudio(Base):
    """Голосовое, которое уходит ученику вместе с уведомлением об уровне
    точки А. Одна запись на уровень (1 или 2), не на ученика — Главный
    преподаватель готовит запись заранее, перезалив обновляет эту же строку
    (`app/services/point_a_level_audio.py::upsert_level_audio`)."""

    __tablename__ = "point_a_level_audios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_s3_path: Mapped[str] = mapped_column(String(500), nullable=False)
    audio_s3_url: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("level IN (1, 2)", name="ck_point_a_level_audios_level"),
        UniqueConstraint("level", name="uq_point_a_level_audios_level"),
    )
