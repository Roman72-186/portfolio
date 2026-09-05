"""Tests for persistent per-user video playback progress."""

from datetime import datetime, timedelta, timezone

from app.constants import (
    VIDEO_WATCH_HEARTBEAT_GAP_CAP_SECONDS,
    VIDEO_WATCH_TOLERANCE_SECONDS,
)
from app.models.video_progress import VideoProgress
from app.services.video_progress import (
    compute_watched_seconds,
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
    )
    completed = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert completed.completed_at is not None
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
    assert rewound.completed_at is not None
    assert get_resume_position(rewound) == 45.0


def test_position_near_end_does_not_resume(db, regular_user):
    save_video_progress(
        db,
        user_id=regular_user.id,
        video_id=VIDEO_ID,
        position_seconds=595.0,
        duration_seconds=600.0,
        completed=False,
    )

    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO_ID)
    assert get_resume_position(progress) == 0


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
    assert compute_watched_seconds(None) == 0.0


def test_compute_watched_seconds_accumulates_over_normal_heartbeat_gap():
    previous = VideoProgress(
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc)  # +10 сек, обычный heartbeat

    assert compute_watched_seconds(previous, now=now) == 60.0


def test_compute_watched_seconds_ignores_gap_longer_than_cap():
    previous = VideoProgress(
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    gap = timedelta(seconds=VIDEO_WATCH_HEARTBEAT_GAP_CAP_SECONDS + 1)
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc) + gap

    # Разрыв длиннее допустимого — трактуется как «ушёл и вернулся», не
    # непрерывный просмотр. Накопленное время не растёт, но и не обнуляется.
    assert compute_watched_seconds(previous, now=now) == 50.0


def test_compute_watched_seconds_ignores_negative_gap():
    """Часы клиента/сервера могут разъехаться — не должно уходить в минус."""
    previous = VideoProgress(
        watched_seconds=50.0,
        updated_at=datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc),
    )
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)  # «в прошлом»

    assert compute_watched_seconds(previous, now=now) == 50.0


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
