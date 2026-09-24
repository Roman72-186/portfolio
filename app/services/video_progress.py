"""Read and atomically upsert per-user video playback progress."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.constants import (
    VIDEO_WATCH_MAX_PLAYBACK_RATE,
    VIDEO_WATCH_NETWORK_SLACK_SECONDS,
    VIDEO_WATCH_TAIL_SECONDS,
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
    # Точная позиция в конце возвращает в начало даже при ещё не подтверждённом
    # просмотре: например, ученик перемотал в конец и ролик закончился раньше,
    # чем набрался порог. Возврат в начало даёт досмотреть вместо тупика на
    # duration/duration. Позицию рядом с концом сохраняем: ученик мог уйти за
    # несколько секунд до `ended`.
    if (
        progress.duration_seconds is not None
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
    """Засчитанные секунды ролика — сумма честных приростов позиции между
    соседними heartbeat'ами.

    Засчитываются секунды ролика, а не секунды на часах (владелец 24.09.2026:
    «ускорение засчитывать»): 10 минут на 2× дают 10 минут. До этого
    засчитывалось реальное время, и ускоренный просмотр за один проход
    порога не набирал.

    Прирост засчитывается не больше, чем ролик физически мог проиграть за
    промежуток между запросами на максимальной скорости плеера, плюс запас на
    задержку сети. Перемотка поэтому даёт не больше честного просмотра на
    2,25×, а пауза, спящая вкладка и перемотка назад не дают ничего: позиция
    не растёт или воспроизведение не активно.

    Кусок урезается, а не выбрасывается целиком (проверка владельца
    24.09.2026): на 2× heartbeat раз в 10 секунд приносит 20 секунд ролика, и
    задержка одного запроса в сети делала следующий «слишком ранним» — весь
    кусок пропадал как перемотка, засчитывалось 65–85% вместо 100%.
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
    allowed = gap * VIDEO_WATCH_MAX_PLAYBACK_RATE + VIDEO_WATCH_NETWORK_SLACK_SECONDS
    return previous.watched_seconds + min(position_delta, allowed)


def watch_threshold_seconds(duration_seconds: float) -> float:
    """До какой секунды досмотреть: «длительность минус 30 секунд». Хвост не
    больше половины ролика, иначе у коротких роликов порог ушёл бы в ноль и
    засчитывался бы просмотр без единой секунды (ревью 05.09.2026)."""
    return duration_seconds - min(VIDEO_WATCH_TAIL_SECONDS, duration_seconds / 2)


@dataclass(frozen=True)
class WatchDecision:
    watched_seconds: float  # всего засчитано за все проходы
    credited_this_pass: float  # засчитано с прошлого зачёта (или с начала)
    threshold_seconds: float | None
    position_reached: bool
    completed: bool


def evaluate_watch(
    previous: VideoProgress | None,
    *,
    position_seconds: float,
    duration_seconds: float | None,
    playback_active: bool,
    ended: bool,
    now: datetime | None = None,
) -> WatchDecision:
    """Засчитан ли просмотр после этого heartbeat'а — одно решение для
    сохранения прогресса (`api/video.py::_save_progress`) и панели проверки у
    суперадмина.

    Засчитано, когда позиция дошла до порога за 30 секунд до конца (или плеер
    прислал `ended`) и честно пройденных секунд ролика набралось столько же.
    После первого зачёта считается только новый проход
    (`last_completion_watched_seconds`, 19.09.2026). Без длительности
    проверить нечего — fail-closed."""
    watched = compute_watched_seconds(
        previous,
        position_seconds=position_seconds,
        playback_active=playback_active or ended,
        now=now,
    )
    credited = watched
    if previous is not None and previous.completed_at is not None:
        credited = max(0.0, watched - previous.last_completion_watched_seconds)
    if duration_seconds is None or duration_seconds <= 0:
        return WatchDecision(watched, credited, None, False, False)
    threshold = watch_threshold_seconds(duration_seconds)
    reached = ended or position_seconds >= threshold
    return WatchDecision(watched, credited, threshold, reached, reached and credited >= threshold)


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
    """Persist position, first completion, and latest confirmed completion.

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
        "last_completed_at": completed_at,
        "last_completion_watched_seconds": (
            (watched_seconds or 0.0) if completed else 0.0
        ),
        "created_at": now,
        "updated_at": now,
    }
    update_values = {
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "completed_at": func.coalesce(VideoProgress.completed_at, completed_at),
        "last_completed_at": (
            completed_at
            if completed
            else VideoProgress.last_completed_at
        ),
        "last_completion_watched_seconds": (
            (
                watched_seconds
                if watched_seconds is not None
                else VideoProgress.watched_seconds
            )
            if completed
            else VideoProgress.last_completion_watched_seconds
        ),
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
            if completed:
                progress.last_completed_at = now
                progress.last_completion_watched_seconds = (
                    watched_seconds
                    if watched_seconds is not None
                    else progress.watched_seconds
                )

    db.commit()
    db.expire_all()
    return completed
