"""JSON-эндпоинт `/cabinet/videos/{id}/embed` — данные для инлайн-показа видео
на АОП (partials/inline/video.html) без перехода на `/cabinet/videos/{id}`.

Должен отдавать те же данные, что и полный рендер страницы, и падать в те же
403/404, что и она — заводить второй путь без проверки доступа нельзя.
"""

from app.config import settings
from app.models.learning_video import LearningVideo
from app.models.video_view_log import VideoViewLog
from app.services.program import ensure_item_topic, set_item_audience
from app.services.tz import today_msk

VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"


def _configure_bunny(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)


def _video_open_to_all(db, user_id: int) -> LearningVideo:
    topic = ensure_item_topic(db, title="Видео недели", day=today_msk(), user_id=user_id)
    set_item_audience(db, topic, assign_to_all=True, tag_ids=[], assignee_ids=[])
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Видео недели",
        topic_id=topic.id,
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def test_embed_returns_player_data_for_group_member(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    video = _video_open_to_all(db, user.id)

    resp = client.get(f"/cabinet/videos/{video.id}/embed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}" in body["player_url"]
    assert body["progress_endpoint"] == f"/cabinet/videos/{video.id}/progress"
    # Мини-опрос из плеера убран 31.08.2026: вопросы к ролику стали блоками
    # задания и показываются общей панелью содержимого на карточке.
    assert "quiz_submit_endpoint" not in body


def test_embed_logs_a_view(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    video = _video_open_to_all(db, user.id)

    client.get(f"/cabinet/videos/{video.id}/embed")

    logs = (
        db.query(VideoViewLog)
        .filter(VideoViewLog.user_id == user.id, VideoViewLog.video_id == VIDEO_ID)
        .all()
    )
    assert len(logs) == 1


def test_embed_forbidden_for_student_outside_group(client, user_factory, session_factory, db, monkeypatch):
    _configure_bunny(monkeypatch)
    user = user_factory(vk_id=222_002, is_group_member=False)
    video = _video_open_to_all(db, user.id)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get(f"/cabinet/videos/{video.id}/embed", headers={"Accept": "application/json"})

    assert resp.status_code == 403
    assert "detail" in resp.json()


def test_embed_not_found_for_unassigned_topic(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    other_topic = ensure_item_topic(db, title="Чужая неделя", day=today_msk(), user_id=999)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Недоступное видео",
        topic_id=other_topic.id,
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    resp = client.get(f"/cabinet/videos/{video.id}/embed")

    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"
