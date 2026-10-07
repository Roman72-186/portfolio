"""Срезы и отказы зачёта видео — почему просмотр не засчитан.

Владелец 07.10.2026: на жалобу «смотрела до конца, а не засчитало» статистику
собирали руками из журнала сервера. Журнал приложению недоступен и живёт
ограниченно, поэтому каждый срезанный кусок (`kind="cut"`) и каждый отказ
кружка (`kind="refusal"`) пишется сюда строкой. Insert-only, как
`VideoViewLog`; `VideoProgress` остаётся текущим состоянием пары
ученик×ролик. Причину раскладывает `services/video_watch_events.py`.

Адрес устройства (IP) сюда не кладётся — это персональные данные.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class VideoWatchEvent(Base):
    __tablename__ = "video_watch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # bunny_video_id, как у VideoProgress и VideoViewLog.
    video_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Блок задания — только у отказа кружка. Без FK: блок удаляют вместе с
    # заданием, а история отказа остаётся.
    block_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # cut | refusal
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # Позиция до и после heartbeat'а (у отказа — только текущая, в `position_to`).
    position_from: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_to: Mapped[float | None] = mapped_column(Float, nullable=True)
    skipped_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    playing: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Засчитано всего после события (у отказа — на момент отказа).
    watched_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_video_watch_events_user_video", "user_id", "video_id", "created_at"),
        Index("ix_video_watch_events_created_at", "created_at"),
    )
