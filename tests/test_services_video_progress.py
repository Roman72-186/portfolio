"""Tests for persistent per-user video playback progress."""

from datetime import datetime, timedelta, timezone

import pytest

from app.constants import VIDEO_WATCH_TAIL_SECONDS
from app.models.video_progress import VideoProgress
from app.models.video_watch_segment import VideoWatchSegment
from app.services.video_progress import (
    LEGACY_SESSION_ID,
    MIGRATED_SESSION_ID,
    RESUME_GAP_LEAD_SECONDS,
    coverage_seconds,
    evaluate_watch,
    get_resume_position,
    get_video_progress,
    load_resume,
    merge_segments,
    pass_segments,
    record_watch,
    save_video_progress,
    watch_shortfall_seconds,
    watch_threshold_seconds,
)


VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"
SECOND_VIDEO_ID = "a9a2f23a-3dd6-4f93-b74e-31dd47e21fe8"
T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def test_progress_upsert_updates_one_row(db, regular_user):
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=35.0,
        duration_seconds=600.0,
        completed=False,
    )
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=91.5,
        duration_seconds=600.0,
        completed=False,
    )

    rows = db.query(VideoProgress).all()
    assert len(rows) == 1
    assert rows[0].position_seconds == 91.5
    assert get_resume_position(rows[0]) == 91.5


def test_progress_is_isolated_by_user_and_video(db, user_factory):
    first_user = user_factory(vk_id=810_001)
    second_user = user_factory(vk_id=810_002)

    save_video_progress(
        db,
        user_id=first_user.id,
        video_id=VIDEO_ID,
        position_seconds=10.0,
        duration_seconds=100.0,
        completed=False,
    )
    save_video_progress(
        db,
        user_id=first_user.id,
        video_id=SECOND_VIDEO_ID,
        position_seconds=20.0,
        duration_seconds=100.0,
        completed=False,
    )
    save_video_progress(
        db,
        user_id=second_user.id,
        video_id=VIDEO_ID,
        position_seconds=30.0,
        duration_seconds=100.0,
        completed=False,
    )

    assert db.query(VideoProgress).count() == 3
    assert get_video_progress(db, user_id=first_user.id, video_id=VIDEO_ID).position_seconds == 10
    assert get_video_progress(db, user_id=first_user.id, video_id=SECOND_VIDEO_ID).position_seconds == 20
    assert get_video_progress(db, user_id=second_user.id, video_id=VIDEO_ID).position_seconds == 30


def test_completion_starts_next_open_at_zero_but_rewatch_can_resume(db, regular_user):
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=600.0,
        duration_seconds=600.0,
        completed=True,
        watched_seconds=600.0,
    )
    completed = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert completed.completed_at is not None
    assert completed.last_completed_at == completed.completed_at
    assert completed.last_completion_watched_seconds == completed.watched_seconds
    first_completed_at = completed.completed_at
    last_completed_at = completed.last_completed_at
    assert get_resume_position(completed) == 0

    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=45.0,
        duration_seconds=600.0,
        completed=False,
    )
    rewound = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert rewound.completed_at == first_completed_at
    assert rewound.last_completed_at == last_completed_at
    assert rewound.last_completion_watched_seconds == completed.watched_seconds
    assert get_resume_position(rewound) == 45.0


def test_rewatch_updates_last_completion_but_preserves_first(db, regular_user):
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=600.0,
        duration_seconds=600.0,
        completed=True,
        watched_seconds=1200.0,
    )
    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    first_completed_at = progress.completed_at
    previous_last_completed_at = first_completed_at - timedelta(days=1)
    progress.last_completed_at = previous_last_completed_at
    progress.last_completion_watched_seconds = 300.0
    db.commit()

    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=600.0,
        duration_seconds=600.0,
        completed=True,
    )

    rewatched = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert rewatched.completed_at == first_completed_at
    assert rewatched.last_completed_at > previous_last_completed_at
    assert rewatched.last_completion_watched_seconds == rewatched.watched_seconds


def test_position_near_end_resumes_when_not_completed(db, regular_user):
    """Ученик ушёл за несколько секунд до конца, покрытия хватает —
    возвращаем на то же место."""
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=595.0,
        duration_seconds=600.0,
        completed=False,
        watched_seconds=590.0,
        covered_seconds=590.0,
    )

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert load_resume(db, progress) == (595.0, None)


def _store_segments(db, user_id, *parts, session="s1"):
    for start, end in parts:
        db.add(VideoWatchSegment(
            user_id=user_id, video_id=VIDEO_ID, session_id=session,
            start_seconds=start, end_seconds=end, updated_at=T0,
        ))
    db.commit()


@pytest.mark.parametrize("position", [575.0, 595.0, 600.0])
def test_unconfirmed_watch_resumes_at_first_missing_part(db, regular_user, position):
    """Развилка 4 (владелец 07.10.2026): дошёл до конца, просмотр не засчитан —
    ставим на первый пропущенный кусок, а не «за N секунд до конца»: досмотр
    хвоста дыру в середине не закрывает. Прод 05.10.2026: возврат на ту же
    позицию у конца давал петлю «досмотрела 5 секунд, порога нет»."""
    save_video_progress(
        db, user_id=regular_user.id, video_id=VIDEO_ID, position_seconds=position,
        duration_seconds=600.0, completed=False, watched_seconds=500.0,
        covered_seconds=500.0,
    )
    _store_segments(db, regular_user.id, (0.0, 300.0), (370.0, 570.0))

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    resume, gap = load_resume(db, progress)
    # Порог 570, не хватает 70 — ровно кусок 5:00–6:10. Старт с запасом.
    assert gap == {"start": 300.0, "end": 370.0, "missing": 70.0, "parts": 2}
    assert resume == 300.0 - RESUME_GAP_LEAD_SECONDS


def test_shortfall_only_when_threshold_reached_and_not_completed(db, regular_user):
    """Подсказку «досмотри кусок» плеер показывает только застрявшему у конца."""
    save_video_progress(
        db, user_id=regular_user.id, video_id=VIDEO_ID, position_seconds=300.0,
        duration_seconds=600.0, completed=False, watched_seconds=100.0, covered_seconds=100.0,
    )
    assert watch_shortfall_seconds(get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)) == 0.0

    save_video_progress(
        db, user_id=regular_user.id, video_id=VIDEO_ID, position_seconds=598.0,
        duration_seconds=600.0, completed=False, watched_seconds=900.0, covered_seconds=500.0,
    )
    # Не хватает по покрытию, а не по сумме: 900 проигранных секунд не спасают.
    assert watch_shortfall_seconds(get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)) == 70.0

    save_video_progress(
        db, user_id=regular_user.id, video_id=VIDEO_ID, position_seconds=600.0,
        duration_seconds=600.0, completed=True, watched_seconds=600.0,
    )
    assert watch_shortfall_seconds(get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)) == 0.0
    assert watch_shortfall_seconds(None) == 0.0


def test_scrubbed_to_end_resumes_near_start(db, regular_user):
    """Перемотал в конец почти без просмотра — досматривать почти всё."""
    save_video_progress(
        db, user_id=regular_user.id, video_id=VIDEO_ID, position_seconds=600.0,
        duration_seconds=600.0, completed=False, watched_seconds=10.0, covered_seconds=10.0,
    )
    _store_segments(db, regular_user.id, (0.0, 10.0))

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    resume, gap = load_resume(db, progress)
    assert gap["start"] == 10.0 and gap["parts"] == 1
    assert resume == 5.0


def test_save_video_progress_persists_watched_seconds(db, regular_user):
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=10.0,
        duration_seconds=600.0,
        completed=False,
        watched_seconds=10.0,
    )

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert progress.watched_seconds == 10.0


def test_save_video_progress_without_watched_seconds_keeps_old_value(db, regular_user):
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=10.0,
        duration_seconds=600.0,
        completed=False,
        watched_seconds=42.0,
    )
    # Второй вызов не передаёт watched_seconds вовсе — колонка не должна
    # обнулиться, старое накопленное время остаётся как есть.
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=20.0,
        duration_seconds=600.0,
        completed=False,
    )

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert progress.watched_seconds == 42.0


# --- правило зачёта: покрытие по отрезкам сеансов ---------------------------
# Владелец 24.09.2026: засчитано, когда позиция дошла до «длительность минус
# 30 секунд»; ускорение засчитывается, перемотка даёт не больше, чем ролик мог
# проиграть на 2,25×. С 07.10.2026 считается покрытие — сколько разных секунд
# ролика просмотрено в проходе, — и отметка сравнивается с прошлой отметкой
# своего сеанса плеера, а не с общей позицией пары.


def _row(watched=0.0, *, completed=False):
    return VideoProgress(
        watched_seconds=watched,
        covered_seconds=0.0,
        completed_at=T0 if completed else None,
        last_completion_watched_seconds=0.0,
    )


def _seg(start, end, *, session="s1", at=T0):
    return VideoWatchSegment(
        session_id=session, start_seconds=start, end_seconds=end, updated_at=at,
    )


def _step(position, *segments, session="s1", after=10, active=True, duration=600.0,
          ended=False, previous=None):
    """Отметка сеанса `session` через `after` секунд после T0."""
    segments = list(segments)
    own = [s for s in segments if s.session_id == session]
    return evaluate_watch(
        previous or _row(), session=own[-1] if own else None, segments=segments,
        position_seconds=position, duration_seconds=duration,
        playback_active=active, ended=ended, now=T0 + timedelta(seconds=after),
    )


def test_first_mark_of_session_credits_nothing_and_opens_segment():
    decision = _step(10.0)
    assert decision.watched_seconds == 0.0
    assert decision.open_at == 10.0
    assert decision.skipped_seconds == 0.0


def test_normal_heartbeat_credits_seconds_of_video():
    decision = _step(60.0, _seg(0.0, 50.0), previous=_row(50.0))
    assert decision.credited_this_pass == 60.0
    assert decision.watched_seconds == 60.0
    assert decision.extend_to == 60.0 and decision.open_at is None


def test_delayed_heartbeat_keeps_its_seconds():
    assert _step(95.0, _seg(0.0, 50.0), after=45).credited_this_pass == 95.0


def test_negative_gap_credits_nothing():
    """Часы клиента и сервера могут разъехаться — не уходим в минус."""
    decision = _step(60.0, _seg(0.0, 50.0), after=-10)
    assert decision.credited_this_pass == 50.0
    assert decision.skipped_seconds == 0.0


def test_paused_request_credits_nothing():
    assert _step(50.0, _seg(0.0, 50.0), after=60, active=False).credited_this_pass == 50.0


def test_seek_back_credits_nothing_and_opens_new_segment():
    decision = _step(100.0, _seg(0.0, 300.0))
    assert decision.credited_this_pass == 300.0
    assert decision.open_at == 100.0
    assert decision.skipped is False


def test_half_speed_credits_seconds_of_video():
    assert _step(55.0, _seg(0.0, 50.0)).credited_this_pass == 55.0


def test_double_speed_credits_seconds_of_video():
    """Владелец 24.09.2026: «ускорение засчитывать». 20 секунд ролика на 2×
    за 10 секунд на часах дают 20."""
    assert _step(70.0, _seg(0.0, 50.0)).credited_this_pass == 70.0


def test_seek_forward_is_capped_at_max_speed():
    """Перемотка вперёд на 450 секунд за 10 секунд даёт не больше, чем ролик
    проиграл бы на 2,25× с запасом на сеть: 10 × 2,25 + 5 = 27,5. Остаток —
    непросмотренный кусок, новый отрезок открывается на месте прыжка."""
    decision = _step(500.0, _seg(0.0, 50.0))
    assert decision.credited_this_pass == 77.5
    assert decision.skipped_seconds == 500.0 - 77.5
    assert decision.open_at == 500.0


def test_double_speed_survives_network_delay():
    """Проверка владельца 24.09.2026: отметка, пришедшая на 3 секунды раньше
    из-за задержки предыдущей, кусок в 20 секунд не теряет."""
    assert _step(120.0, _seg(0.0, 100.0), after=7).credited_this_pass == 120.0


def test_threshold_is_thirty_seconds_before_end():
    assert watch_threshold_seconds(600.0) == 600.0 - VIDEO_WATCH_TAIL_SECONDS


def test_threshold_tail_is_capped_for_short_videos():
    """Регрессия (ревью 05.09.2026): у ролика короче хвоста порог ушёл бы в
    ноль и засчитывал бы просмотр без единой секунды."""
    assert watch_threshold_seconds(20.0) == 10.0


def test_completes_thirty_seconds_before_end():
    decision = _step(570.0, _seg(0.0, 560.0))
    assert decision.threshold_seconds == 570.0
    assert decision.completed is True


def test_not_completed_before_tail():
    assert _step(560.0, _seg(0.0, 550.0)).completed is False


def test_seek_into_tail_does_not_complete():
    decision = _step(590.0, _seg(0.0, 100.0))
    assert decision.position_reached is True
    assert decision.completed is False


def test_short_video_needs_half_of_it():
    """У 20-секундного ролика порог 10 с: 5 секунд с ended не засчитываются."""
    decision = _step(5.0, _seg(0.0, 0.0), duration=20.0, ended=True)
    assert decision.threshold_seconds == 10.0
    assert decision.completed is False


def test_without_duration_never_completes():
    """Нет длительности — проверить нечего, fail-closed."""
    assert _step(570.0, _seg(0.0, 560.0), duration=None, ended=True).completed is False


def test_ended_before_tail_completes_when_covered():
    """Плеер прислал `ended`, покрытие выше порога — засчитано, даже если
    последняя отметка была раньше 30-секундного хвоста."""
    decision = _step(600.0, _seg(0.0, 575.0), ended=True, after=4)
    assert decision.completed is True


def test_seek_forward_while_playing_warns_student():
    """Владелец 06.10.2026: ученику говорим о перемотке сразу."""
    assert _step(500.0, _seg(0.0, 50.0)).skipped is True


def test_seek_forward_while_paused_warns_student():
    """На паузе ничего не засчитывается: позиция ушла вперёд без проигрывания."""
    decision = _step(300.0, _seg(0.0, 50.0), active=False)
    assert decision.skipped is True
    assert decision.credited_this_pass == 50.0


def test_double_speed_with_network_delay_does_not_warn():
    assert _step(120.0, _seg(0.0, 100.0), after=3).skipped is False
    assert _step(120.0, _seg(0.0, 100.0), after=10).skipped is False


def test_short_nudge_forward_does_not_warn():
    assert _step(110.0, _seg(0.0, 100.0), active=False).skipped is False


def test_resume_on_another_device_is_not_a_cut():
    """Прод 06.10.2026: продолжение с места выглядит как прыжок 0→242 — плеер
    на старте присылает 0. Новый сеанс на другом устройстве: эти секунды уже
    просмотрены, ни среза, ни предупреждения."""
    earlier = _seg(0.0, 258.0, session="phone", at=T0 - timedelta(hours=5))
    decision = _step(242.0, earlier, _seg(0.0, 0.0, session="laptop"), session="laptop", after=6)
    assert decision.skipped_seconds == 0.0
    assert decision.skipped is False
    assert decision.credited_this_pass == 258.0


def test_skip_beyond_watched_part_still_warns():
    earlier = _seg(0.0, 258.0, session="phone")
    decision = _step(500.0, earlier, _seg(0.0, 0.0, session="laptop"), session="laptop", after=6)
    assert decision.skipped is True
    assert decision.skipped_seconds == 500.0 - 258.0


def test_completing_heartbeat_does_not_warn():
    """Зачёт важнее: если просмотр засчитан, о перескоке не говорим."""
    decision = _step(590.0, _seg(0.0, 565.0), _seg(0.0, 570.0, session="s2"), after=1)
    assert decision.completed is True
    assert decision.skipped is False


def test_merge_counts_overlap_once_and_joins_small_gaps():
    merged = merge_segments([(0, 100), (50, 150), (150.5, 200), (300, 310)])
    assert merged == [(0, 200), (300, 310)]
    assert coverage_seconds(merged) == 210


def test_merge_clips_to_duration():
    assert merge_segments([(590, 610)], duration_seconds=600.0) == [(590, 600.0)]


# --- сценарии из разбора 07.10.2026: с базой и записью отрезков --------------


def _beat(db, user_id, session, position, at, *, active=True, ended=False, duration=600.0):
    decision, _ = record_watch(
        db, user_id=user_id, video_id=VIDEO_ID, session_id=session,
        position_seconds=position, duration_seconds=duration,
        playback_active=active, ended=ended, now=T0 + timedelta(seconds=at),
    )
    return decision


def _watch(db, user_id, session, start, end, at, *, step=10, rate=1.0, duration=600.0):
    """Ровный просмотр сеанса от `start` до `end`, отметка каждые `step` с."""
    position = start
    decision = _beat(db, user_id, session, position, at, duration=duration)
    while position < end:
        at += step
        position = min(end, position + step * rate)
        decision = _beat(db, user_id, session, position, at, duration=duration)
    return decision, at


def test_two_players_at_once_neither_lose_nor_inflate(db, regular_user):
    """Владелец 06.10.2026, 18:14 UTC: две вкладки одного ролика, позиции
    «пилой» 46→110, 56→110, 66→122. Раньше отстающий получал ноль, передний —
    до 2,25×. Теперь каждый считает от своей отметки: срезов нет, покрытие —
    склейка того, что реально проиграно."""
    uid = regular_user.id
    _beat(db, uid, "tab-a", 36.0, 0)
    _beat(db, uid, "tab-b", 100.0, 1)
    decisions = []
    for i in range(1, 4):
        decisions.append(_beat(db, uid, "tab-a", 36.0 + 10 * i, 10 * i))
        decisions.append(_beat(db, uid, "tab-b", 100.0 + 10 * i, 10 * i + 1))
    assert all(d.skipped_seconds == 0 for d in decisions)
    assert all(d.skipped is False for d in decisions)
    # tab-a: 36–66, tab-b: 100–130 — 60 секунд разных кусков, не больше.
    progress = get_video_progress(db, user_id=uid, video_id=VIDEO_ID)
    assert progress.covered_seconds == 60.0


def test_forgotten_tab_does_not_cut_live_device(db, regular_user):
    """Забытая вкладка час назад стояла на 50 и вдруг прислала то же место —
    честный кусок с другого устройства не срезан."""
    uid = regular_user.id
    _beat(db, uid, "old-tab", 40.0, -3600)
    _beat(db, uid, "old-tab", 50.0, -3590)
    _beat(db, uid, "phone", 300.0, 0)
    _beat(db, uid, "old-tab", 50.0, 5, active=False)
    decision = _beat(db, uid, "phone", 310.0, 10)
    assert decision.skipped_seconds == 0.0
    assert decision.credited_this_pass == 20.0


def test_rewatching_start_four_times_then_seek_to_end_is_not_credited(db, regular_user):
    """Дыра суммы секунд: первые 5 минут 20-минутного ролика четыре раза
    (1200 проигранных секунд — больше порога 1170) и перемотка в конец.
    Покрытие — 300 секунд, зачёта нет, предупреждение есть."""
    uid = regular_user.id
    at = 0
    for _ in range(4):
        _, at = _watch(db, uid, "tab", 0.0, 300.0, at, duration=1200.0)
        at += 10
    decision = _beat(db, uid, "tab", 1190.0, at + 10, duration=1200.0)
    assert decision.watched_seconds >= watch_threshold_seconds(1200.0)
    # 300 секунд начала плюс то, что прыжок мог честно проиграть за 20 с.
    assert decision.credited_this_pass <= 300.0 + 20 * 2.25 + 5
    assert decision.completed is False
    assert decision.skipped is True


def test_resume_on_other_device_continues_without_cut(db, regular_user):
    uid = regular_user.id
    _watch(db, uid, "phone", 0.0, 300.0, 0)
    _beat(db, uid, "laptop", 0.0, 1000)
    decision = _beat(db, uid, "laptop", 300.0, 1006)
    assert decision.skipped_seconds == 0.0
    decision, _ = _watch(db, uid, "laptop", 300.0, 570.0, 1006)
    assert decision.completed is True


def test_double_speed_on_slow_network_completes(db, regular_user):
    """2× с неровными промежутками между отметками — засчитано целиком."""
    uid = regular_user.id
    at, position = 0, 0.0
    decision = _beat(db, uid, "tab", position, at)
    for gap in [10, 7, 13, 10, 6, 14] * 5:
        at += gap
        position = min(600.0, position + gap * 2)
        decision = _beat(db, uid, "tab", position, at)
        if decision.completed:
            break
    assert decision.completed is True


def test_new_pass_after_completion_starts_from_zero(db, regular_user):
    """Развилка 3: зачёт стирает отрезки — ролик в новом занятии требует нового
    просмотра, старый проход перемотку не прикрывает."""
    uid = regular_user.id
    decision, at = _watch(db, uid, "tab", 0.0, 570.0, 0)
    assert decision.completed is True
    progress = get_video_progress(db, user_id=uid, video_id=VIDEO_ID)
    assert progress.covered_seconds == 0.0
    assert pass_segments(db, user_id=uid, video_id=VIDEO_ID) == []
    _beat(db, uid, "tab2", 0.0, at + 100)
    _beat(db, uid, "tab2", 10.0, at + 110)
    decision = _beat(db, uid, "tab2", 575.0, at + 120)
    assert decision.completed is False
    assert decision.skipped is True


def test_legacy_mark_without_session_still_counts(db, regular_user):
    """Вкладка со старым скриптом не шлёт номер сеанса — идёт общим сеансом."""
    uid = regular_user.id
    decision, _ = _watch(db, uid, LEGACY_SESSION_ID, 0.0, 100.0, 0)
    assert decision.credited_this_pass == 100.0


def test_migrated_seconds_count_as_watched_from_start(db, regular_user):
    """Развилка 2: накопленное до перехода — отрезок от начала ролика. Ученик
    продолжает с места и засчитывается досмотром остатка."""
    uid = regular_user.id
    save_video_progress(
        db, user_id=uid, video_id=VIDEO_ID, position_seconds=600.0, duration_seconds=1200.0,
        completed=False, watched_seconds=600.0, covered_seconds=600.0,
    )
    _store_segments(db, uid, (0.0, 600.0), session=MIGRATED_SESSION_ID)
    decision, _ = _watch(db, uid, "tab", 600.0, 1170.0, 0, duration=1200.0)
    assert decision.completed is True


def test_empty_session_segment_moves_instead_of_new_rows(db, regular_user):
    uid = regular_user.id
    _beat(db, uid, "tab", 0.0, 0)
    _beat(db, uid, "tab", 300.0, 1, active=False)
    _beat(db, uid, "tab", 100.0, 2, active=False)
    segments = pass_segments(db, user_id=uid, video_id=VIDEO_ID)
    assert [(s.start_seconds, s.end_seconds) for s in segments] == [(100.0, 100.0)]
