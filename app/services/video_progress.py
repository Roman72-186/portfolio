"""Read and atomically upsert per-user video playback progress."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.constants import (
    VIDEO_WATCH_MAX_PLAYBACK_RATE,
    VIDEO_WATCH_MIN_PLAYBACK_RATE,
    VIDEO_WATCH_POSITION_JITTER_SECONDS,
    VIDEO_WATCH_TOLERANCE_SECONDS,
    VIDEO_WATCH_TRIAL_TAIL_SECONDS,
)
from app.models.video_progress import VideoProgress

# Запас на задержку сети для пробного правила (владелец 24.09.2026, проверка
# на 2×: засчиталось 65–85% вместо 100%). Heartbeat идёт раз в 10 секунд, на
# 2× ролик за это время проходит 20 секунд, а предел был 10 × 2,25 + 2 = 24,5.
# Стоило одному запросу задержаться в сети на пару секунд, следующий приходил
# «слишком рано» и весь кусок в 20 секунд выбрасывался как перемотка. Теперь
# кусок не выбрасывается, а урезается до того, что ролик физически мог
# проиграть, плюс этот запас. Живет здесь, а не в `constants.py`, пока
# правило пробное.
TRIAL_NETWORK_SLACK_SECONDS = 5
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
    # просмотре. Такая строка возникает, если ролик закончился раньше, чем
    # накопилось требуемое календарное время (например, на скорости 2×). Возврат
    # в начало даёт добрать время вместо тупика на duration/duration. Позицию рядом с
    # концом сохраняем: ученик мог уйти за несколько секунд до `ended`.
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
    credit_playback_speed: bool = False,
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
    if credit_playback_speed:
        # Пробное правило: засчитываем пройденные секунды ролика (ускорение в
        # плюс), но не больше, чем ролик мог проиграть на максимальной
        # скорости за этот промежуток. Перемотка даёт не больше честного
        # просмотра на 2,25×, а задержанный сетью heartbeat почти ничего не
        # теряет — см. `TRIAL_NETWORK_SLACK_SECONDS`.
        allowed = gap * VIDEO_WATCH_MAX_PLAYBACK_RATE + TRIAL_NETWORK_SLACK_SECONDS
        return previous.watched_seconds + min(position_delta, allowed)
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


def trial_threshold_seconds(duration_seconds: float) -> float:
    """Пробное правило: до какой секунды досмотреть — «длительность минус
    30 секунд», но хвост не больше половины ролика, иначе у коротких роликов
    порог ушёл бы в ноль."""
    return duration_seconds - min(VIDEO_WATCH_TRIAL_TAIL_SECONDS, duration_seconds / 2)


@dataclass(frozen=True)
class TrialWatchDecision:
    watched_seconds: float  # всего засчитано за все проходы
    credited_this_pass: float  # засчитано с прошлого зачёта (или с начала)
    threshold_seconds: float | None
    position_reached: bool
    completed: bool


def evaluate_trial_watch(
    previous: VideoProgress | None,
    *,
    position_seconds: float,
    duration_seconds: float | None,
    playback_active: bool,
    ended: bool,
    now: datetime | None = None,
) -> TrialWatchDecision:
    """Решение пробного правила по одному heartbeat'у — для страницы проверки
    у суперадмина и её панели, одна функция на оба места.

    Засчитано, когда позиция дошла до порога за 30 секунд до конца (или
    плеер прислал `ended`) и честно пройденных секунд ролика набралось
    столько же. После первого зачёта считается только новый проход, как и в
    живом правиле (`last_completion_watched_seconds`)."""
    watched = compute_watched_seconds(
        previous,
        position_seconds=position_seconds,
        playback_active=playback_active or ended,
        now=now,
        credit_playback_speed=True,
    )
    credited = watched
    if previous is not None and previous.completed_at is not None:
        credited = max(0.0, watched - previous.last_completion_watched_seconds)
    if duration_seconds is None or duration_seconds <= 0:
        return TrialWatchDecision(watched, credited, None, False, False)
    threshold = trial_threshold_seconds(duration_seconds)
    reached = ended or position_seconds >= threshold
    return TrialWatchDecision(watched, credited, threshold, reached, reached and credited >= threshold)


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
