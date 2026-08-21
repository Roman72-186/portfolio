"""Видео → трекер: автозакрытие задачи и лог возвратов к плееру.

Первое достижение `completed_at` у VideoProgress должно один раз закрыть
TrackerTask(kind=video) той же темы, причём auto — не перезаписывать ручное
закрытие и не срабатывать повторно на каждый heartbeat. Каждое открытие
страницы плеера пишет строку в video_view_logs — источник счётчика возвратов.
"""

from app.config import settings
from app.models.learning_video import LearningVideo
from app.models.tracker import ITEM_VIDEO, STATUS_DONE, TrackerTaskState
from app.models.video_view_log import VideoViewLog
from app.services.program import ensure_item_topic, set_item_audience
from app.services.tracker import create_task
from app.services.tz import today_msk

VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"


def _configure_bunny(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)


def _video_with_task(db, user_id: int, *, duration_seconds: float = 600.0):
    topic = ensure_item_topic(db, title="Видео дня", day=today_msk(), user_id=user_id)
    set_item_audience(db, topic, assign_to_all=True, tag_ids=[], assignee_ids=[])
    task = create_task(
        db, title="Видео дня", user_id=user_id, topic_id=topic.id, kind=ITEM_VIDEO,
    )
    task.is_published = True
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Видео дня",
        topic_id=topic.id,
        status="ready",
        is_published=True,
        duration_seconds=duration_seconds,
    )
    db.add(video)
    db.commit()
    db.refresh(task)
    db.refresh(video)
    return task, video


def test_first_completion_closes_tracker_task(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    task, video = _video_with_task(db, user.id)

    resp = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )
    assert resp.status_code == 200
    assert resp.json()["completed"] is True

    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task.id, TrackerTaskState.user_id == user.id)
        .one()
    )
    assert state.status == STATUS_DONE
    assert state.completion_source == "auto"
    assert state.completed_by_id is None


def test_manual_close_is_not_reopened_by_later_heartbeat(auth_client, db, monkeypatch):
    """Ручная отметка «сделано» не должна откатываться авто-событием позже."""
    client, user = auth_client
    _configure_bunny(monkeypatch)
    task, video = _video_with_task(db, user.id)

    manual = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert manual.status_code == 200
    assert manual.json()["status"] == "done"

    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )

    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task.id, TrackerTaskState.user_id == user.id)
        .one()
    )
    assert state.status == STATUS_DONE
    assert state.completion_source is None  # закрыто вручную, авто не тронуло


def test_repeated_heartbeat_after_completion_does_not_error(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    task, video = _video_with_task(db, user.id)

    first = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )
    second = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 599, "duration_seconds": 600},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    states = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task.id, TrackerTaskState.user_id == user.id)
        .all()
    )
    assert len(states) == 1


def test_incomplete_progress_does_not_close_task(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    task, video = _video_with_task(db, user.id)

    resp = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 10, "duration_seconds": 600},
    )
    assert resp.status_code == 200
    assert resp.json()["completed"] is False
    assert db.query(TrackerTaskState).count() == 0


def test_opening_player_logs_a_view(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    _, video = _video_with_task(db, user.id)

    client.get(f"/cabinet/videos/{video.id}")
    client.get(f"/cabinet/videos/{video.id}")

    logs = (
        db.query(VideoViewLog)
        .filter(VideoViewLog.user_id == user.id, VideoViewLog.video_id == VIDEO_ID)
        .all()
    )
    assert len(logs) == 2
