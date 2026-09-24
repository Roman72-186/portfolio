"""Tests for persistent per-user video playback progress."""

from datetime import datetime, timedelta, timezone

from app.constants import VIDEO_WATCH_TAIL_SECONDS
from app.models.video_progress import VideoProgress
from app.services.video_progress import (
    compute_watched_seconds,
    evaluate_watch,
    get_resume_position,
    get_video_progress,
    save_video_progress,
    watch_threshold_seconds,
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


# --- правило зачёта (владелец 24.09.2026) -----------------------------------
# Засчитано, когда позиция дошла до «длительность минус 30 секунд» и честно
# проигранных секунд ролика набралось столько же. Ускорение засчитывается,
# перемотка даёт не больше, чем ролик мог проиграть на 2,25×.

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _row(position, watched, *, completed=False, last_pass=0.0):
    return VideoProgress(
        position_seconds=position,
        watched_seconds=watched,
        updated_at=T0,
        completed_at=T0 if completed else None,
        last_completion_watched_seconds=last_pass,
    )


def _credit(previous, position, *, after=10, active=True):
    return compute_watched_seconds(
        previous, position_seconds=position, playback_active=active,
        now=T0 + timedelta(seconds=after),
    )


def _decide(previous, position, *, duration=600.0, ended=False, after=10):
    return evaluate_watch(
        previous, position_seconds=position, duration_seconds=duration,
        playback_active=True, ended=ended, now=T0 + timedelta(seconds=after),
    )


def test_compute_watched_seconds_no_previous_row_is_zero():
    assert compute_watched_seconds(None, position_seconds=10.0, playback_active=True) == 0.0


def test_normal_heartbeat_credits_seconds_of_video():
    assert _credit(_row(50.0, 50.0), 60.0) == 60.0


def test_delayed_heartbeat_keeps_its_seconds():
    assert _credit(_row(50.0, 50.0), 95.0, after=45) == 95.0


def test_negative_gap_credits_nothing():
    """Часы клиента и сервера могут разъехаться — не уходим в минус."""
    assert _credit(_row(50.0, 50.0), 60.0, after=-10) == 50.0


def test_paused_request_credits_nothing():
    assert _credit(_row(50.0, 50.0), 50.0, after=60, active=False) == 50.0


def test_seek_back_credits_nothing():
    assert _credit(_row(300.0, 300.0), 100.0) == 300.0


def test_half_speed_credits_seconds_of_video():
    assert _credit(_row(50.0, 50.0), 55.0) == 55.0


def test_double_speed_credits_seconds_of_video():
    """Владелец 24.09.2026: «ускорение засчитывать». 20 секунд ролика на 2×
    за 10 секунд на часах дают 20, а не 10, как было до этого."""
    assert _credit(_row(50.0, 50.0), 70.0) == 70.0


def test_seek_forward_is_capped_at_max_speed():
    """Перемотка вперёд на 450 секунд за 10 секунд даёт не больше, чем ролик
    проиграл бы на 2,25× с запасом на сеть: 10 × 2,25 + 5 = 27,5."""
    assert _credit(_row(50.0, 50.0), 500.0) == 77.5


def test_double_speed_survives_network_delay():
    """Проверка владельца 24.09.2026: на 2× засчитывалось 65–85%. Heartbeat,
    пришедший на 3 секунды раньше из-за задержки предыдущего, раньше
    выбрасывал весь кусок в 20 секунд. Теперь засчитывается целиком."""
    assert _credit(_row(100.0, 100.0), 120.0, after=7) == 120.0


def test_threshold_is_thirty_seconds_before_end():
    assert watch_threshold_seconds(600.0) == 600.0 - VIDEO_WATCH_TAIL_SECONDS


def test_threshold_tail_is_capped_for_short_videos():
    """Регрессия (ревью 05.09.2026): у ролика короче хвоста порог ушёл бы в
    ноль и засчитывал бы просмотр без единой секунды."""
    assert watch_threshold_seconds(20.0) == 10.0


def test_completes_thirty_seconds_before_end():
    decision = _decide(_row(560.0, 560.0), 570.0)
    assert decision.threshold_seconds == 570.0
    assert decision.completed is True


def test_not_completed_before_tail():
    assert _decide(_row(550.0, 550.0), 560.0).completed is False


def test_seek_into_tail_does_not_complete():
    decision = _decide(_row(100.0, 100.0), 590.0)
    assert decision.position_reached is True
    assert decision.completed is False


def test_double_speed_full_pass_completes():
    """Полный проход на 2× с неровными промежутками между heartbeat'ами."""
    row = _row(0.0, 0.0)
    now = T0
    position = 0.0
    decision = None
    for gap in [10, 7, 13, 10, 6, 14] * 5:
        now = now + timedelta(seconds=gap)
        position += gap * 2
        if position > 580:
            break
        decision = evaluate_watch(
            row, position_seconds=position, duration_seconds=600.0,
            playback_active=True, ended=False, now=now,
        )
        row = VideoProgress(
            position_seconds=position, watched_seconds=decision.watched_seconds,
            updated_at=now, completed_at=None, last_completion_watched_seconds=0.0,
        )
    assert decision.completed is True


def test_rewatch_needs_a_fresh_pass():
    """После первого зачёта считается только новый проход."""
    assert _decide(_row(560.0, 1160.0, completed=True, last_pass=600.0), 570.0).completed is True
    assert _decide(_row(560.0, 700.0, completed=True, last_pass=600.0), 570.0).completed is False


def test_short_video_needs_half_of_it():
    """У 20-секундного ролика порог 10 с: 5 секунд с ended не засчитываются."""
    decision = _decide(_row(0.0, 0.0), 5.0, duration=20.0, ended=True)
    assert decision.threshold_seconds == 10.0
    assert decision.completed is False


def test_without_duration_never_completes():
    """Нет длительности — проверить нечего, fail-closed."""
    assert _decide(_row(560.0, 560.0), 570.0, duration=None, ended=True).completed is False
