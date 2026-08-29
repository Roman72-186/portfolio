"""Фоновая проверка статуса видео (exam_scheduler.py::_run_video_status_sync).

Раньше статус подтягивался только пока была открыта страница «Загрузка
видео» — если куратор закрывал вкладку до конца обработки, запись зависала
на `processing` навсегда, и опубликовать ролик (auto_publish_on_ready) было
некому (живой баг, найден 29.08.2026). Эта job — замена того ручного клика.
"""
from app.config import settings
from app.models.learning_video import LearningVideo
from app.services.exam_scheduler import _run_video_status_sync


def _video(db, **overrides):
    values = {
        "bunny_library_id": 720058,
        "bunny_video_id": "35ed80ae-8103-4528-a700-3f69ec56957d",
        "title": "Видео недели",
        "status": "processing",
        "is_published": False,
    }
    values.update(overrides)
    video = LearningVideo(**values)
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def _stub_remote(monkeypatch, **fields):
    payload = {"status": 4, "encodeProgress": 100, "length": 120.0, "transcodingMessages": []}
    payload.update(fields)
    monkeypatch.setattr("app.services.video_catalog.get_video", lambda video_id: payload)


def test_sync_job_publishes_flagged_videos_once_ready(db, monkeypatch, regular_user):
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    video = _video(db, auto_publish_on_ready=True, created_by_id=regular_user.id)
    other = _video(db, bunny_video_id="other-guid", auto_publish_on_ready=False)
    _stub_remote(monkeypatch)

    _run_video_status_sync()

    db.expire_all()
    video = db.get(LearningVideo, video.id)
    other = db.get(LearningVideo, other.id)
    assert video.status == "ready"
    assert video.is_published is True
    # Автопубликация приписывается тому, кто поставил ролик в день
    # (create_video_item пишет created_by_id) — не системному/пустому автору.
    assert video.published_by_id == regular_user.id
    assert other.status == "ready"
    assert other.is_published is False


def test_sync_job_ignores_videos_already_terminal(db, monkeypatch):
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    ready_video = _video(db, status="ready", bunny_video_id="ready-guid")
    calls = []
    monkeypatch.setattr(
        "app.services.video_catalog.get_video",
        lambda video_id: calls.append(video_id) or {"status": 4, "encodeProgress": 100},
    )

    _run_video_status_sync()

    assert calls == []
    db.expire_all()
    assert db.get(LearningVideo, ready_video.id).status == "ready"


def test_sync_job_does_nothing_when_bunny_disabled(db, monkeypatch):
    monkeypatch.setattr(settings, "bunny_stream_enabled", False)
    video = _video(db, auto_publish_on_ready=True)
    calls = []
    monkeypatch.setattr(
        "app.services.video_catalog.get_video",
        lambda video_id: calls.append(video_id) or {"status": 4},
    )

    _run_video_status_sync()

    assert calls == []
    db.expire_all()
    assert db.get(LearningVideo, video.id).status == "processing"


def test_sync_job_survives_a_single_video_failure(db, monkeypatch):
    """Один упавший запрос к Bunny не должен остановить обработку остальных
    видео в той же выборке."""
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    from app.services.bunny_stream import BunnyStreamAPIError

    broken = _video(db, bunny_video_id="broken-guid", auto_publish_on_ready=True)
    healthy = _video(db, bunny_video_id="healthy-guid", auto_publish_on_ready=True)

    def fake_get_video(video_id):
        if video_id == "broken-guid":
            raise BunnyStreamAPIError("boom", status_code=502)
        return {"status": 4, "encodeProgress": 100, "length": 60.0, "transcodingMessages": []}

    monkeypatch.setattr("app.services.video_catalog.get_video", fake_get_video)

    _run_video_status_sync()

    db.expire_all()
    assert db.get(LearningVideo, broken.id).status == "processing"
    assert db.get(LearningVideo, healthy.id).is_published is True
