"""Read and atomically upsert per-user video playback progress."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.constants import (
    VIDEO_WATCH_MAX_PLAYBACK_RATE,
    VIDEO_WATCH_NETWORK_SLACK_SECONDS,
    VIDEO_WATCH_SEGMENT_JOIN_SECONDS,
    VIDEO_WATCH_SKIP_NOTICE_SECONDS,
    VIDEO_WATCH_TAIL_SECONDS,
)
from app.models.video_progress import VideoProgress
from app.models.video_view_log import VideoViewLog
from app.models.video_watch_segment import VideoWatchSegment


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


# --- отрезки просмотра (07.10.2026) -----------------------------------------
# Зачёт считает покрытие текущего прохода — сколько разных секунд ролика
# просмотрено, — а не сумму проигранных секунд. Модель и почему — в докстринге
# `models/video_watch_segment.py`, план — `plans/2026-10-07-apparchi-видео-отрезки.md`.

# Сеанс отметок старого скрипта без номера (вкладки, открытые до выкатки).
LEGACY_SESSION_ID = ""
# Секунды, накопленные до перехода на отрезки (миграция `b123ee32347b`).
MIGRATED_SESSION_ID = "migrated"

Interval = tuple[float, float]


def merge_segments(
    intervals: Iterable[Interval], *, duration_seconds: float | None = None
) -> list[Interval]:
    """Склейка отрезков: наложения считаются один раз, зазор до
    `VIDEO_WATCH_SEGMENT_JOIN_SECONDS` дырой не считается. Отрезки обрезаются
    по ролику; пустые выпадают."""
    upper = duration_seconds if duration_seconds and duration_seconds > 0 else None
    items = sorted(
        (max(0.0, start), end if upper is None else min(end, upper))
        for start, end in intervals
    )
    merged: list[Interval] = []
    for start, end in items:
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + VIDEO_WATCH_SEGMENT_JOIN_SECONDS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def coverage_seconds(merged: list[Interval]) -> float:
    return sum(end - start for start, end in merged)


def uncovered_seconds(merged: list[Interval], start: float, end: float) -> float:
    """Сколько секунд из [start, end] не покрыто склейкой."""
    if end <= start:
        return 0.0
    covered = sum(
        max(0.0, min(end, b) - max(start, a)) for a, b in merged
    )
    return max(0.0, (end - start) - covered)


def missing_parts(merged: list[Interval], duration_seconds: float) -> list[Interval]:
    """Непросмотренные куски ролика по порядку — дыры склейки и хвост."""
    gaps: list[Interval] = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor > VIDEO_WATCH_SEGMENT_JOIN_SECONDS:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration_seconds - cursor > VIDEO_WATCH_SEGMENT_JOIN_SECONDS:
        gaps.append((cursor, duration_seconds))
    return gaps


def lock_video_progress(
    db: DBSession, *, user_id: int, video_id: str
) -> VideoProgress | None:
    """Строка пары под блокировкой до коммита: отметки двух плееров одной пары
    иначе читали бы одно и то же покрытие и затирали бы друг другу кэш.
    SQLite блокировку игнорирует."""
    return (
        db.query(VideoProgress)
        .filter(VideoProgress.user_id == user_id, VideoProgress.video_id == video_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )


def pass_segments(db: DBSession, *, user_id: int, video_id: str) -> list[VideoWatchSegment]:
    """Отрезки текущего прохода пары — после зачёта их стирает
    `apply_watch_segments`, поэтому «текущий проход» — это все строки."""
    return (
        db.query(VideoWatchSegment)
        .filter(VideoWatchSegment.user_id == user_id, VideoWatchSegment.video_id == video_id)
        .order_by(VideoWatchSegment.id)
        .all()
    )


def session_segment(
    segments: list[VideoWatchSegment], session_id: str
) -> VideoWatchSegment | None:
    """Последний отрезок сеанса — от него считается следующая отметка."""
    for segment in reversed(segments):
        if segment.session_id == session_id:
            return segment
    return None


def watch_shortfall_seconds(progress: VideoProgress | None) -> float:
    """Сколько просмотра не хватает до зачёта, если ученик уже дошёл до
    порога, а просмотр не засчитан; иначе 0. По нему `resume_gap` ставит
    ученика на первый пропущенный кусок, а плеер говорит, зачем (05.10.2026,
    с 07.10.2026 — по покрытию)."""
    if progress is None or progress.completed_at is not None or not progress.duration_seconds:
        return 0.0
    threshold = watch_threshold_seconds(progress.duration_seconds)
    if progress.position_seconds < threshold:
        return 0.0
    return max(0.0, threshold - (progress.covered_seconds or 0.0))


# На сколько раньше пропущенного куска ставить ученика. Отрезок открывается
# первой отметкой после «плей», а если событие `play` не дошло, плеер выводит
# «играет» из хода позиции за 2 с (до 4,5 с ролика на 2,25×) — без запаса
# начало дыры так и осталось бы непросмотренным.
RESUME_GAP_LEAD_SECONDS = 5.0


def resume_gap(
    progress: VideoProgress | None, segments: list[VideoWatchSegment] | None
) -> dict | None:
    """Первый пропущенный кусок, если ученик дошёл до порога без зачёта:
    `start`/`end` куска, `missing` — сколько всего досмотреть, `parts` — сколько
    кусков пропущено. Иначе None."""
    missing = watch_shortfall_seconds(progress)
    if missing <= 0 or segments is None:
        return None
    duration = progress.duration_seconds
    merged = merge_segments(
        ((s.start_seconds, s.end_seconds) for s in segments), duration_seconds=duration
    )
    gaps = missing_parts(merged, duration)
    if not gaps:
        return None
    start, end = gaps[0]
    return {
        "start": round(start, 1),
        "end": round(end, 1),
        "missing": round(missing, 1),
        "parts": len(gaps),
    }


def load_resume(db: DBSession, progress: VideoProgress | None) -> tuple[float, dict | None]:
    """Позиция возврата и пропущенный кусок — отрезки читаются, только когда
    ученик дошёл до порога без зачёта."""
    segments = None
    if watch_shortfall_seconds(progress) > 0:
        segments = pass_segments(db, user_id=progress.user_id, video_id=progress.video_id)
    gap = resume_gap(progress, segments)
    return get_resume_position(progress, gap), gap


def get_resume_position(progress: VideoProgress | None, gap: dict | None = None) -> float:
    if progress is None:
        return 0.0
    # Дошёл до порога, а покрытия не хватает — ставим на первый пропущенный
    # кусок (развилка 4, владелец 07.10.2026). До этого ставили «за столько
    # секунд до конца, сколько не хватает» (05.10.2026, прод: 66 пар застряли
    # петлёй «досмотрела 5 секунд, обновила — снова в конце»), но досмотр
    # хвоста не закрывает дыру в середине ролика.
    if gap is not None:
        return round(max(0.0, gap["start"] - RESUME_GAP_LEAD_SECONDS), 1)
    if progress.position_seconds < 5:
        return 0.0
    duration = progress.duration_seconds
    # Засчитанный ролик, досмотренный до конца, начинается сначала. Позицию
    # рядом с концом сохраняем: ученик мог уйти за несколько секунд до `ended`.
    if duration is not None and progress.position_seconds >= duration:
        return 0.0
    return round(progress.position_seconds, 1)


def view_state(progress: VideoProgress | None) -> str:
    """Отметка ролика в списке: `completed`, `started` или `new`.

    Одно правило на каталог (`/cabinet/videos`) и архив ученика
    (`/cabinet/learning/archive`, 04.10.2026)."""
    if progress is not None and progress.completed_at:
        return "completed"
    return "started" if get_resume_position(progress) >= 5 else "new"


def watch_threshold_seconds(duration_seconds: float) -> float:
    """До какой секунды досмотреть: «длительность минус 30 секунд». Хвост не
    больше половины ролика, иначе у коротких роликов порог ушёл бы в ноль и
    засчитывался бы просмотр без единой секунды (ревью 05.09.2026)."""
    return duration_seconds - min(VIDEO_WATCH_TAIL_SECONDS, duration_seconds / 2)


@dataclass(frozen=True)
class WatchDecision:
    watched_seconds: float  # всего засчитано за все проходы — для статистики
    credited_this_pass: float  # покрытие текущего прохода после отметки
    threshold_seconds: float | None
    position_reached: bool
    completed: bool
    # Непросмотренная часть прыжка вперёд не меньше порога
    # `VIDEO_WATCH_SKIP_NOTICE_SECONDS` — плеер предупреждает ученика.
    skipped: bool = False
    # Сколько секунд прыжка вперёд осталось непросмотренными — ни этим
    # сеансом, ни другими. Прыжок внутрь просмотренного (продолжение с места,
    # второй плеер) срезом не считается.
    skipped_seconds: float = 0.0
    # Позиция прошлой отметки этого сеанса и пауза до неё — для разбора среза.
    position_from: float | None = None
    gap_seconds: float | None = None
    # Что записать: новый конец последнего отрезка сеанса и/или новый отрезок
    # с позиции отметки (`apply_watch_segments`).
    extend_to: float | None = None
    open_at: float | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def evaluate_watch(
    previous: VideoProgress | None,
    *,
    session: VideoWatchSegment | None,
    segments: list[VideoWatchSegment],
    position_seconds: float,
    duration_seconds: float | None,
    playback_active: bool,
    ended: bool,
    now: datetime | None = None,
) -> WatchDecision:
    """Засчитан ли просмотр после этой отметки — одно решение для сохранения
    прогресса (`api/video.py::_save_progress`) и панели проверки у суперадмина.

    `session` — последний отрезок сеанса, приславшего отметку (None — первая
    отметка сеанса), `segments` — все отрезки текущего прохода, он среди них.

    Отметка сравнивается с прошлой отметкой **своего сеанса**, а не с общей
    позицией пары (07.10.2026): иначе второй плеер срезал первому честный
    кусок. Прирост засчитывается не больше, чем ролик мог проиграть за паузу
    между отметками на 2,25×, плюс запас на сеть (владелец 24.09.2026:
    «ускорение засчитывать»); на паузе — ничего. Кусок урезается, а не
    выбрасывается: на 2× задержка одного запроса иначе стоила всего куска.
    Шаг назад, перемотка и пауза со сдвигом открывают новый отрезок.

    Засчитано, когда позиция дошла до порога за 30 секунд до конца (или пришёл
    `ended`) и покрытие текущего прохода — сколько разных секунд ролика
    просмотрено — не меньше порога. Пересмотр одного и того же куска покрытие
    не растит. Без длительности проверить нечего — fail-closed."""
    playing = playback_active or ended
    now = now or datetime.now(timezone.utc)
    credited = 0.0
    position_from = gap = extend_to = open_at = jump_from = None
    if session is None:
        open_at = position_seconds
    else:
        position_from = session.end_seconds
        delta = position_seconds - position_from
        updated_at = _aware(session.updated_at)
        gap = (now - updated_at).total_seconds() if updated_at is not None else None
        if delta < 0:
            open_at = position_seconds
        elif delta == 0:
            pass
        elif not playing:
            # Позиция ушла вперёд без проигрывания — перемотка на паузе
            # целиком (06.10.2026, для предупреждения ученику).
            jump_from = position_from
            open_at = position_seconds
        elif gap is None or gap <= 0:
            # Часы разъехались — ни засчитать, ни срезать нечем.
            open_at = position_seconds
        else:
            allowed = gap * VIDEO_WATCH_MAX_PLAYBACK_RATE + VIDEO_WATCH_NETWORK_SLACK_SECONDS
            credited = min(delta, allowed)
            extend_to = position_from + credited
            if credited < delta:
                jump_from = extend_to
                open_at = position_seconds

    after = [
        (s.start_seconds, extend_to if s is session and extend_to is not None else s.end_seconds)
        for s in segments
    ]
    merged = merge_segments(after, duration_seconds=duration_seconds)
    covered = coverage_seconds(merged)
    skipped_seconds = (
        uncovered_seconds(merged, jump_from, position_seconds) if jump_from is not None else 0.0
    )
    skipped = skipped_seconds >= VIDEO_WATCH_SKIP_NOTICE_SECONDS
    watched = (previous.watched_seconds if previous is not None else 0.0) + credited
    details = dict(
        position_from=position_from, gap_seconds=gap, extend_to=extend_to, open_at=open_at,
    )
    if duration_seconds is None or duration_seconds <= 0:
        return WatchDecision(
            watched, covered, None, False, False, skipped, skipped_seconds, **details,
        )
    threshold = watch_threshold_seconds(duration_seconds)
    reached = ended or position_seconds >= threshold
    completed = reached and covered >= threshold
    return WatchDecision(
        watched, covered, threshold, reached, completed,
        skipped and not completed, skipped_seconds, **details,
    )


def apply_watch_segments(
    db: DBSession,
    *,
    user_id: int,
    video_id: str,
    session_id: str,
    session: VideoWatchSegment | None,
    decision: WatchDecision,
    now: datetime,
) -> None:
    """Записать шаг отрезков в текущую транзакцию — коммитится вместе с
    прогрессом. Зачёт стирает отрезки пары: следующий просмотр копит новый
    проход (развилка 3, владелец 07.10.2026). Пустой последний отрезок сеанса
    переезжает на новое место, а не плодит строки."""
    if decision.completed:
        db.query(VideoWatchSegment).filter(
            VideoWatchSegment.user_id == user_id, VideoWatchSegment.video_id == video_id,
        ).delete(synchronize_session=False)
        return
    if session is not None:
        if decision.extend_to is not None:
            session.end_seconds = decision.extend_to
        session.updated_at = now
    if decision.open_at is None:
        return
    if session is not None and session.end_seconds <= session.start_seconds:
        session.start_seconds = session.end_seconds = decision.open_at
        return
    db.add(VideoWatchSegment(
        user_id=user_id,
        video_id=video_id,
        session_id=session_id,
        start_seconds=decision.open_at,
        end_seconds=decision.open_at,
        created_at=now,
        updated_at=now,
    ))


def record_watch(
    db: DBSession,
    *,
    user_id: int,
    video_id: str,
    session_id: str,
    position_seconds: float,
    duration_seconds: float | None,
    playback_active: bool,
    ended: bool,
    now: datetime | None = None,
) -> tuple[WatchDecision, bool]:
    """Отметка плеера целиком: строка пары под блокировкой, отрезки прохода,
    решение `evaluate_watch`, запись отрезков и прогресса одним коммитом.
    Возвращает решение и был ли ролик засчитан до этой отметки.

    Блокировка — потому что два плеера одной пары иначе считали бы покрытие
    от одного и того же состояния и затирали бы друг другу кэш (07.10.2026).
    """
    now = now or datetime.now(timezone.utc)
    existing = lock_video_progress(db, user_id=user_id, video_id=video_id)
    segments = pass_segments(db, user_id=user_id, video_id=video_id)
    session = session_segment(segments, session_id)
    was_completed = existing is not None and existing.completed_at is not None
    decision = evaluate_watch(
        existing,
        session=session,
        segments=segments,
        position_seconds=position_seconds,
        duration_seconds=duration_seconds,
        playback_active=playback_active,
        ended=ended,
        now=now,
    )
    apply_watch_segments(
        db,
        user_id=user_id,
        video_id=video_id,
        session_id=session_id,
        session=session,
        decision=decision,
        now=now,
    )
    save_video_progress(
        db,
        user_id=user_id,
        video_id=video_id,
        position_seconds=position_seconds,
        duration_seconds=duration_seconds,
        completed=decision.completed,
        watched_seconds=decision.watched_seconds,
        # Зачёт начинает новый проход — покрытие с нуля, отрезки стёрты.
        covered_seconds=0.0 if decision.completed else decision.credited_this_pass,
    )
    return decision, was_completed


def save_video_progress(
    db: DBSession,
    *,
    user_id: int,
    video_id: str,
    position_seconds: float,
    duration_seconds: float | None,
    completed: bool,
    watched_seconds: float | None = None,
    covered_seconds: float | None = None,
) -> bool:
    """Persist position, first completion, and latest confirmed completion.

    `watched_seconds` (сумма засчитанного за все проходы) и `covered_seconds`
    (покрытие текущего прохода) уже посчитаны `evaluate_watch`. `None`
    оставляет колонку как есть при обновлении и заводит с нуля при первой
    строке — прямым юнит-тестам этой функции они не нужны.
    """
    now = datetime.now(timezone.utc)
    completed_at = now if completed else None
    insert_values = {
        "user_id": user_id,
        "video_id": video_id,
        "position_seconds": position_seconds,
        "duration_seconds": duration_seconds,
        "watched_seconds": watched_seconds or 0.0,
        "covered_seconds": covered_seconds or 0.0,
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
    if covered_seconds is not None:
        update_values["covered_seconds"] = covered_seconds

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
            if covered_seconds is not None:
                progress.covered_seconds = covered_seconds
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
