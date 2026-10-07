"""Просмотренные отрезки ролика — по сеансам плеера (07.10.2026).

Зачёт считает покрытие: сколько разных секунд ролика просмотрено в текущем
проходе, наложения один раз. До этого `VideoProgress.watched_seconds` копил
сумму честных секунд, и у суммы было две беды: все плееры ученика сравнивались
с одной общей позицией (второй плеер срезал первому честный кусок), а
пересмотр начала копил секунды так же, как новый кусок ролика.

Строка — непрерывный отрезок одного сеанса (экземпляра плеера на странице).
Сеанс продлевает свой последний отрезок, разрыв (перемотка, пауза со сдвигом,
шаг назад) открывает новый. Режет отрезки сервер
(`services/video_progress.py::evaluate_watch`), плеер присылает только номер
сеанса. Покрытие текущего прохода кэшируется в `VideoProgress.covered_seconds`;
при зачёте отрезки пары удаляются — следующий просмотр копит новый проход.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class VideoWatchSegment(Base):
    __tablename__ = "video_watch_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # bunny_video_id, как у VideoProgress.
    video_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Номер сеанса от плеера. Пустая строка — отметки старого скрипта без
    # номера (вкладки, открытые до выкатки); `migrated` — секунды, накопленные
    # до перехода на отрезки.
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    # До куда отрезок засчитан. Он же — позиция последней отметки сеанса.
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # Время последней отметки сеанса по часам сервера — от него считается,
    # сколько ролик мог проиграть до следующей.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_video_watch_segments_user_video", "user_id", "video_id"),
    )
