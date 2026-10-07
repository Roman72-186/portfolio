"""Почему просмотр видео не засчитан: причина среза и отказа кружка — одно место.

Владелец 07.10.2026: статистика незачёта по каждому ученику в карточке и на
«Статистике активности». До этого срезы и отказы жили только строками журнала
сервера, и раскладывали их по причинам руками. Здесь — классификация
(`classify_cut`, `refusal_reason`) и запись строк `VideoWatchEvent`; считают
по ним `activity_stats.get_video_watch_stats` и `get_video_loss_stats`.

Решение о зачёте не здесь, а в `video_progress.evaluate_watch`: этот модуль
только объясняет, почему кусок не засчитан, и правила зачёта не меняет.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from app.constants import (
    VIDEO_WATCH_MAX_PLAYBACK_RATE,
    VIDEO_WATCH_NETWORK_SLACK_SECONDS,
    VIDEO_WATCH_SKIP_NOTICE_SECONDS,
)
from app.models.video_progress import VideoProgress
from app.models.video_watch_event import VideoWatchEvent
from app.services.video_progress import watch_threshold_seconds

logger = logging.getLogger(__name__)

KIND_CUT = "cut"
KIND_REFUSAL = "refusal"

# Причины среза. Порядок проверки — в `classify_cut`.
CUT_PAUSED_PLAYING = "paused_playing"
CUT_NETWORK = "network"
CUT_RESUME = "resume"
CUT_INSIDE_CREDITED = "inside_credited"
CUT_SEEK = "seek"

CUT_LABELS = {
    CUT_PAUSED_PLAYING: "Плеер считал паузой",
    CUT_SEEK: "Перемотка",
    CUT_NETWORK: "Сеть",
    CUT_RESUME: "Продолжение с места остановки",
    CUT_INSIDE_CREDITED: "Скачок по засчитанному",
}
# Скачок внутрь уже засчитанного: секунды засчитаны раньше, просмотр от
# такого среза не теряет ничего. Прод 06.10.2026: больше половины срезанных
# секунд за день — эти два класса, и в одной цифре с настоящими потерями они
# пугали бы минутами, которые ни на что не влияют. С переходом на отрезки
# (07.10.2026) новые строки этих классов не пишутся: срез — только
# непросмотренные секунды, а прыжок внутрь просмотренного их не даёт. Класс
# остался для строк до перехода.
HARMLESS_CUTS = frozenset({CUT_RESUME, CUT_INSIDE_CREDITED})
# Сбой плеера, а не поведение ученика: после починки 07.10.2026 (`b30fe01`,
# «играет» по ходу позиции) класс должен уйти в ноль. Не ушёл — плеер снова
# сломался; это видно на «Статистике активности».
PLAYER_FAULT_CUTS = frozenset({CUT_PAUSED_PLAYING})

# Причины отказа кружка — те же условия, что `cabinet_tracker._video_block_watched`.
REFUSAL_NO_VIDEO = "no_video"
REFUSAL_NOT_STARTED = "not_started"
REFUSAL_COMPLETED_BEFORE_BLOCK = "completed_before_block"
REFUSAL_NO_DURATION = "no_duration"
REFUSAL_BELOW_THRESHOLD = "below_threshold"
REFUSAL_SHORTFALL = "shortfall"

REFUSAL_LABELS = {
    REFUSAL_NO_VIDEO: "Ролика нет",
    REFUSAL_NOT_STARTED: "Ролик не запускал",
    REFUSAL_COMPLETED_BEFORE_BLOCK: "Засчитан раньше, нужен новый проход",
    REFUSAL_NO_DURATION: "У ролика нет длительности",
    REFUSAL_BELOW_THRESHOLD: "Не досмотрел до конца",
    REFUSAL_SHORTFALL: "Дошёл до конца, но не хватило секунд",
}

# Ниже этой скорости позиция на «паузе» не похожа на идущий ролик.
PAUSED_PLAYING_MIN_RATE = 0.5

# Позиция, с которой плеер стартует: «прыжок с нуля» на место остановки.
RESUME_START_MAX_SECONDS = 5.0


def classify_cut(
    *,
    position_from: float,
    position_to: float,
    skipped_seconds: float,
    gap_seconds: float | None,
    playing: bool,
    credited_before: float | None = None,
) -> str:
    """Причина срезанного куска. Порядок проверки важен.

    1. Плеер прислал «не играет», а позиция шла как часы — не медленнее
       половины скорости и не быстрее 2,25× за паузу между отметками: ролик
       шёл, а флаг врал (ученица id 229, 06.10.2026: два полных просмотра
       потеряно так, «10→20 за 10 с»). Нижняя граница отделяет первую
       отметку после долгого простоя («1→10 через 7 часов»).
    2. Играло, и срезано меньше порога предупреждения — запрос задержался в
       сети, кусок урезан запасом `evaluate_watch`.
    3. Позиция не ушла дальше засчитанного в этом проходе — скачок внутрь
       засчитанного: с нуля — продолжение с места остановки, иначе «скачок по
       засчитанному». Второй плеер того же ролика («пила» у владельца
       06.10.2026) и перемотка внутри уже просмотренного по позициям не
       различить (прод: 97→955 при засчитанных 1062), поэтому класс один. То же
       условие, что не даёт `evaluate_watch` предупреждать о перемотке.
    4. Остальное — перемотка вперёд.

    Шаг 3 — только для строк до перехода на отрезки (`credited_before` даёт
    перенос журнала): с 07.10.2026 срез уже несёт только непросмотренные
    секунды, и прыжок внутрь просмотренного срезом не бывает.
    """
    delta = position_to - position_from
    if (
        not playing
        and gap_seconds is not None
        and gap_seconds > 0
        and gap_seconds * PAUSED_PLAYING_MIN_RATE
        <= delta
        <= gap_seconds * VIDEO_WATCH_MAX_PLAYBACK_RATE + VIDEO_WATCH_NETWORK_SLACK_SECONDS
    ):
        return CUT_PAUSED_PLAYING
    if playing and skipped_seconds < VIDEO_WATCH_SKIP_NOTICE_SECONDS:
        return CUT_NETWORK
    if credited_before is not None and position_to <= credited_before + VIDEO_WATCH_SKIP_NOTICE_SECONDS:
        if position_from <= RESUME_START_MAX_SECONDS:
            return CUT_RESUME
        return CUT_INSIDE_CREDITED
    return CUT_SEEK


def cut_event(
    *,
    user_id: int,
    video_id: str,
    position_from: float,
    position_to: float,
    skipped_seconds: float,
    gap_seconds: float | None,
    playing: bool,
    watched_seconds: float,
    duration_seconds: float | None,
) -> VideoWatchEvent:
    """Строка среза. `position_from` — прошлая отметка того же сеанса плеера,
    `watched_seconds` — покрытие прохода после отметки."""
    return VideoWatchEvent(
        user_id=user_id,
        video_id=video_id,
        kind=KIND_CUT,
        reason=classify_cut(
            position_from=position_from,
            position_to=position_to,
            skipped_seconds=skipped_seconds,
            gap_seconds=gap_seconds,
            playing=playing,
        ),
        position_from=position_from,
        position_to=position_to,
        skipped_seconds=skipped_seconds,
        gap_seconds=gap_seconds,
        playing=playing,
        watched_seconds=watched_seconds,
        duration_seconds=duration_seconds,
    )


def refusal_reason(
    progress: VideoProgress | None,
    *,
    video_exists: bool,
    duration_seconds: float | None,
) -> str:
    """Почему кружок видео-блока не поставлен. Те же условия, что у
    `cabinet_tracker._video_block_watched`, по порядку."""
    if not video_exists:
        return REFUSAL_NO_VIDEO
    if progress is None:
        return REFUSAL_NOT_STARTED
    if progress.last_completed_at or progress.completed_at:
        return REFUSAL_COMPLETED_BEFORE_BLOCK
    if not duration_seconds:
        return REFUSAL_NO_DURATION
    if progress.position_seconds < watch_threshold_seconds(duration_seconds):
        return REFUSAL_BELOW_THRESHOLD
    return REFUSAL_SHORTFALL


def refusal_event(
    progress: VideoProgress | None,
    *,
    user_id: int,
    video_id: str,
    block_id: int,
    reason: str,
    duration_seconds: float | None,
) -> VideoWatchEvent:
    return VideoWatchEvent(
        user_id=user_id,
        video_id=video_id,
        block_id=block_id,
        kind=KIND_REFUSAL,
        reason=reason,
        position_to=progress.position_seconds if progress is not None else None,
        # Покрытие прохода на момент отказа — по нему `refusal_text` считает,
        # сколько не хватило.
        watched_seconds=progress.covered_seconds if progress is not None else None,
        duration_seconds=duration_seconds,
    )


def record(db: DBSession, event: VideoWatchEvent) -> None:
    """Записать событие отдельным коммитом. Сбой записи статистики не должен
    ронять сохранение прогресса и ответ ученику — откат и строка в лог."""
    try:
        db.add(event)
        db.commit()
    except SQLAlchemyError:
        logger.exception(
            "Видео: событие незачёта не записано | user=%s | video=%s | %s",
            event.user_id, event.video_id, event.reason,
        )
        db.rollback()


def _mmss(seconds: float | None) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def refusal_text(event: VideoWatchEvent) -> str:
    """Причина отказа простыми словами, с цифрами — для карточки ученика."""
    label = REFUSAL_LABELS.get(event.reason, event.reason)
    duration = event.duration_seconds
    if event.reason == REFUSAL_BELOW_THRESHOLD and duration:
        return (
            f"Не досмотрел до конца: дошёл до {_mmss(event.position_to)}"
            f", нужно до {_mmss(watch_threshold_seconds(duration))}"
        )
    if event.reason == REFUSAL_SHORTFALL and duration:
        missing = watch_threshold_seconds(duration) - (event.watched_seconds or 0)
        return f"Дошёл до конца, но не досмотрел {_mmss(max(0.0, missing))} – пропущенные куски"
    return label
