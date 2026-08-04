"""Tests for persistent per-user video playback progress."""

from app.models.video_progress import VideoProgress
from app.services.video_progress import (
    get_resume_position,
    get_video_progress,
    save_video_progress,
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
