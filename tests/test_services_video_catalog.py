from app.models.learning_video import LearningVideo
from app.services.video_catalog import list_published_videos, publish_video, unpublish_video


def _video(**overrides):
    values = {
        "bunny_library_id": 720058,
        "bunny_video_id": "35ed80ae-8103-4528-a700-3f69ec56957d",
        "title": "Урок",
        "status": "ready",
        "is_published": False,
    }
    values.update(overrides)
    return LearningVideo(**values)


def test_catalogue_exposes_only_ready_published_videos(db, monkeypatch):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    published = _video(is_published=True, sort_order=2)
    processing = _video(
        bunny_video_id="a9a2f23a-3dd6-4f93-b74e-31dd47e21fe8",
        status="processing",
        is_published=True,
        sort_order=1,
    )
    hidden = _video(
        bunny_video_id="db60eaef-6898-4ec3-bf35-5f2d151239b7",
        is_published=False,
        sort_order=0,
    )
    db.add_all([published, processing, hidden])
    db.commit()

    assert list_published_videos(db) == [published]


def test_catalogue_does_not_resurrect_legacy_pilot_after_unpublish(db, monkeypatch):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: True)
    hidden = _video(is_published=False)
    db.add(hidden)
    db.commit()

    assert list_published_videos(db) == []


def test_publish_requires_ready_and_unpublish_clears_publication(db, regular_user):
    video = _video(status="processing")
    db.add(video)
    db.commit()

    try:
        publish_video(video, user_id=regular_user.id)
        assert False, "processing video must not publish"
    except ValueError:
        pass

    video.status = "ready"
    publish_video(video, user_id=regular_user.id)
    assert video.is_published is True
    assert video.published_at is not None
    assert video.published_by_id == regular_user.id

    unpublish_video(video)
    assert video.is_published is False
    assert video.published_at is None
