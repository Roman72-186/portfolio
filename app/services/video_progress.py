"""Read and atomically upsert per-user video playback progress."""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.constants import (
    VIDEO_WATCH_MAX_PLAYBACK_RATE,
    VIDEO_WATCH_MIN_PLAYBACK_RATE,
    VIDEO_WATCH_POSITION_JITTER_SECONDS,
    VIDEO_WATCH_TOLERANCE_SECONDS,
)
from app.models.video_progress import VideoProgress
from app.models.video_view_log import VideoViewLog


def log_video_view(db: DBSession, *, user_id: int, video_id: str) -> None:
    """Insert-only отметка «открыл плеер» — источник счётчика возвратов.

    Пишется на каждое открытие страницы, включая первое: «сколько раз
    возвращался» читатель считает как `count - 1`, а «когда именно» видно по
    `opened_at`, чего не даёт счётчик поверх VideoProgress.
    """
    db.add(VideoViewLog(user_id=user_id, video_id=video_id))
    db.commit()


def count_video_views(db: DBSession, *, user_id: int, video_id: str) -> int:
    return (
        db.query(VideoViewLog.id)
        .filter(VideoViewLog.user_id == user_id, VideoViewLog.video_id == video_id)
        .count()
    )


def get_video_progress(
    db: DBSession,
    *,
    user_id: int,
    video_id: str,
) -> VideoProgress | None:
    return db.get(VideoProgress, (user_id, video_id))


def get_resume_position(progress: VideoProgress | None) -> float:
    if progress is None or progress.position_seconds < 5:
        return 0.0
    # Возвращаемся в начало только после подтверждённого события `ended` и
    # позиции в самом конце. Близкая к концу позиция сама по себе не означает
    # завершение: ученик мог обновить страницу за несколько секунд до конца и
    # должен продолжить с сохранённого места. После начала повторного просмотра
    # completed_at сохраняется исторически, поэтому позиция снова возобновляется.
    if (
        progress.completed_at is not None
        and progress.duration_seconds is not None
        and progress.position_seconds >= progress.duration_seconds
    ):
        return 0.0
    return round(progress.position_seconds, 1)


def compute_watched_seconds(
    previous: VideoProgress | None,
    *,
    position_seconds: float,
    playback_active: bool,
    now: datetime | None = None,
) -> float:
    """Накопленное реальное время просмотра — не позиция плеера, а сумма
    промежутков календарного времени между соседними heartbeat'ами.

    Позицию (`position_seconds`) можно перемотать одним движением ползунка
    или отправить руками — она ничего не говорит о том, сколько секунд
    ролик реально был на экране. Этот счётчик — про то самое реальное время,
    защита от перемотки строится на нём (владелец 05.09.2026).

    Одного календарного интервала недостаточно: так открытый на ночь плеер
    засчитал бы часы. Поэтому сервер также требует активное воспроизведение и
    правдоподобное движение позиции. Задержка сети не теряет время, а пауза,
    спящая вкладка и перемотка ничего не добавляют.
    """
    if previous is None:
        return 0.0
    if not playback_active:
        return previous.watched_seconds
    now = now or datetime.now(timezone.utc)
    updated_at = previous.updated_at
    if updated_at is None:
        return previous.watched_seconds
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    gap = (now - updated_at).total_seconds()
    if gap <= 0:
        return previous.watched_seconds
    position_delta = position_seconds - previous.position_seconds
    if position_delta <= 0:
        return previous.watched_seconds
    if position_delta > (
        gap * VIDEO_WATCH_MAX_PLAYBACK_RATE + VIDEO_WATCH_POSITION_JITTER_SECONDS
    ):
        return previous.watched_seconds
    credited = min(gap, position_delta / VIDEO_WATCH_MIN_PLAYBACK_RATE)
    return previous.watched_seconds + credited


def watched_enough(watched_seconds: float, duration_seconds: float | None) -> bool:
    """Досмотрел по реальному времени — независимо от скорости
    воспроизведения (владелец 05.09.2026, формулировка ровно такая: «длина
    видео на любой скорости равна времени просмотра, с погрешностью
    30–40 сек»). Без длительности проверить нечего — fail-closed.

    Допуск ограничен половиной длительности ролика (ревью 05.09.2026, найдено
    после первой реализации): без этого у любого ролика короче
    `VIDEO_WATCH_TOLERANCE_SECONDS` (35 сек) порог уходил в отрицательные
    числа, и `watched_seconds >= отрицательное` было истиной уже при нуле —
    перемотка в конец короткого ролика проходила с первого heartbeat'а, без
    единой секунды реального просмотра. Для роликов длиннее 70 сек допуск
    остаётся ровно 35 сек, как просил владелец, ничего не меняется."""
    if duration_seconds is None or duration_seconds <= 0:
        return False
    tolerance = min(VIDEO_WATCH_TOLERANCE_SECONDS, duration_seconds / 2)
    return watched_seconds >= duration_seconds - tolerance


def save_video_progress(
    db: DBSession,
    *,
    user_id: int,
    video_id: str,
    position_seconds: float,
    duration_seconds: float | None,
    completed: bool,
    watched_seconds: float | None = None,
) -> bool:
    """Persist the latest position and preserve the first completion timestamp.

    `watched_seconds` — накопленное реальное время просмотра (см.
    `compute_watched_seconds`), уже посчитанное вызывающим кодом. `None`
    (по умолчанию, как звали эту функцию до 05.09.2026) оставляет колонку
    как есть при обновлении и заводит с нуля при первой строке — вызывающий
    код, которому анти-скрабинг не нужен (например прямые юнит-тесты этой
    функции), не обязан её знать.
    """
    now = datetime.now(timezone.utc)
    completed_at = now if completed else None
    insert_values = {
        "user_id": user_id,
        "video_id": video_id,
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "watched_seconds": watched_seconds or 0.0,
        "completed_at": completed_at,
        "created_at": now,
        "updated_at": now,
    }
    update_values = {
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "completed_at": func.coalesce(VideoProgress.completed_at, completed_at),
        "updated_at": now,
    }
    if watched_seconds is not None:
        update_values["watched_seconds"] = watched_seconds

    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(VideoProgress).values(**insert_values)
        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "video_id"],
            set_=update_values,
        )
        db.execute(statement)
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        statement = insert(VideoProgress).values(**insert_values)
        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "video_id"],
            set_=update_values,
        )
        db.execute(statement)
    else:
        progress = get_video_progress(db, user_id=user_id, video_id=video_id)
        if progress is None:
            db.add(VideoProgress(**insert_values))
        else:
            progress.position_seconds = position_seconds
            progress.duration_seconds = duration_seconds
            if watched_seconds is not None:
                progress.watched_seconds = watched_seconds
            progress.updated_at = now
            if completed and progress.completed_at is None:
                progress.completed_at = now

    db.commit()
    db.expire_all()
    return completed
