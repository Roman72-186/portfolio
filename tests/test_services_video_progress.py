"""Tests for persistent per-user video playback progress."""

from datetime import datetime, timedelta, timezone

from app.constants import VIDEO_WATCH_TOLERANCE_SECONDS
from app.models.video_progress import VideoProgress
from app.services.video_progress import (
    compute_watched_seconds,
    evaluate_trial_watch,
    get_resume_position,
    get_video_progress,
    save_video_progress,
    watched_enough,
)


VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"
SECOND_VIDEO_ID = "a9a2f23a-3dd6-4f93-b74e-31dd47e21fe8"


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
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=595.0,
        duration_seconds=600.0,
        completed=False,
    )

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert get_resume_position(progress) == 595.0


def test_position_at_end_restarts_when_watch_time_is_not_completed(db, regular_user):
    """Ролик может дойти до ended раньше порога watched_seconds на
    ускорении. Возобновление с duration/duration не дало бы добрать
    недостающее календарное время.
    """
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=600.0,
        duration_seconds=600.0,
        completed=False,
        watched_seconds=300.0,
    )

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert progress.completed_at is None
    assert get_resume_position(progress) == 0.0


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


# --- защита от перемотки: compute_watched_seconds / watched_enough ---------


def test_compute_watched_seconds_no_previous_row_is_zero():
    assert compute_watched_seconds(
        None, position_seconds=10.0, playback_active=True
    ) == 0.0


def test_compute_watched_seconds_accumulates_over_normal_heartbeat_gap():
    previous = VideoProgress(
        position_seconds=50.0,
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc)  # +10 сек, обычный heartbeat

    assert compute_watched_seconds(
        previous, position_seconds=60.0, playback_active=True, now=now
    ) == 60.0


def test_compute_watched_seconds_keeps_honest_delayed_heartbeat():
    previous = VideoProgress(
        position_seconds=50.0,
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    gap = timedelta(seconds=45)
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc) + gap

    assert compute_watched_seconds(
        previous, position_seconds=95.0, playback_active=True, now=now
    ) == 95.0


def test_compute_watched_seconds_ignores_negative_gap():
    """Часы клиента/сервера могут разъехаться — не должно уходить в минус."""
    previous = VideoProgress(
        position_seconds=50.0,
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)  # «в прошлом»

    assert compute_watched_seconds(
        previous, position_seconds=60.0, playback_active=True, now=now
    ) == 50.0


def test_compute_watched_seconds_ignores_paused_requests():
    previous = VideoProgress(
        position_seconds=50.0,
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 1, 0, tzinfo=timezone.utc)

    assert compute_watched_seconds(
        previous, position_seconds=50.0, playback_active=False, now=now
    ) == 50.0


def test_compute_watched_seconds_rejects_seek_jump():
    previous = VideoProgress(
        position_seconds=50.0,
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc)

    assert compute_watched_seconds(
        previous, position_seconds=500.0, playback_active=True, now=now
    ) == 50.0


def test_compute_watched_seconds_supports_half_speed():
    previous = VideoProgress(
        position_seconds=50.0,
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc)

    assert compute_watched_seconds(
        previous, position_seconds=55.0, playback_active=True, now=now
    ) == 60.0


def test_compute_watched_seconds_supports_double_speed_without_double_credit():
    previous = VideoProgress(
        position_seconds=50.0,
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc)

    assert compute_watched_seconds(
        previous, position_seconds=70.0, playback_active=True, now=now
    ) == 60.0


def test_watched_enough_requires_close_to_full_duration():
    duration = 600.0
    threshold = duration - VIDEO_WATCH_TOLERANCE_SECONDS

    assert watched_enough(threshold, duration) is True
    assert watched_enough(threshold - 1, duration) is False


def test_watched_enough_without_duration_is_fail_closed():
    assert watched_enough(1000.0, None) is False


def test_watched_enough_caps_tolerance_for_short_videos():
    """Регрессия (ревью 05.09.2026): для ролика короче допуска
    duration - VIDEO_WATCH_TOLERANCE_SECONDS уходит в минус, и просмотр
    засчитывался бы уже при watched_seconds=0 — перемотка в конец короткого
    ролика проходила бы без единой секунды реального просмотра."""
    duration = 20.0  # короче VIDEO_WATCH_TOLERANCE_SECONDS (35)

    assert watched_enough(0.0, duration) is False
    assert watched_enough(duration / 2, duration) is True  # допуск не больше половины ролика


def test_scrubbing_to_the_end_does_not_complete_without_watch_time():
    """Перемотка ползунком в конец не должна засчитывать просмотр даже
    когда позиция формально у конца ролика — реального времени не набралось."""
    duration = 600.0
    watched_seconds = 5.0  # только что открыл, тут же перемотал в конец

    assert watched_enough(watched_seconds, duration) is False


# --- пробное правило суперадмина (владелец 24.09.2026) -----------------------
# Только для страницы проверки моста: зачёт за 30 секунд до конца, ускорение
# засчитывается. Живое правило учеников выше не меняется.

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _row(position, watched, *, completed=False, last_pass=0.0):
    return VideoProgress(
        position_seconds=position,
        watched_seconds=watched,
        updated_at=T0,
        completed_at=T0 if completed else None,
        last_completion_watched_seconds=last_pass,
    )


def _trial(previous, position, *, duration=600.0, ended=False):
    return evaluate_trial_watch(
        previous, position_seconds=position, duration_seconds=duration,
        playback_active=True, ended=ended, now=T0 + timedelta(seconds=10),
    )


def test_trial_completes_thirty_seconds_before_end():
    decision = _trial(_row(560.0, 560.0), 570.0)
    assert decision.threshold_seconds == 570.0
    assert decision.completed is True


def test_trial_not_completed_before_tail():
    assert _trial(_row(550.0, 550.0), 560.0).completed is False


def test_trial_credits_double_speed():
    """20 секунд ролика за 10 секунд на часах дают 20, а не 10."""
    assert _trial(_row(50.0, 50.0), 70.0).watched_seconds == 70.0


def test_trial_seek_into_tail_does_not_complete():
    decision = _trial(_row(100.0, 100.0), 590.0)
    assert decision.position_reached is True
    assert decision.watched_seconds == 100.0
    assert decision.completed is False


def test_trial_rewatch_needs_a_fresh_pass():
    assert _trial(_row(560.0, 1160.0, completed=True, last_pass=600.0), 570.0).completed is True
    assert _trial(_row(560.0, 700.0, completed=True, last_pass=600.0), 570.0).completed is False


def test_trial_short_video_tail_is_capped():
    """Хвост не больше половины ролика: у 20-секундного порог 10 с, и 5
    проигранных секунд с `ended` зачёта не дают."""
    decision = _trial(_row(0.0, 0.0), 5.0, duration=20.0, ended=True)
    assert decision.threshold_seconds == 10.0
    assert decision.completed is False


def test_trial_without_duration_never_completes():
    assert _trial(_row(560.0, 560.0), 570.0, duration=None, ended=True).completed is False


def test_live_rule_is_unchanged_by_the_trial():
    """Ученики по-прежнему: засчитывается реальное время, 2× не удваивает."""
    previous = _row(50.0, 50.0)
    assert compute_watched_seconds(
        previous, position_seconds=70.0, playback_active=True,
        now=T0 + timedelta(seconds=10),
    ) == 60.0
