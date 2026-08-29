"""Мини-опрос из уточняющих вопросов после видео — маршруты ученика.

Досмотр решает сервер (`VideoProgress.completed_at`), не клиент — тот же
принцип, что у автозакрытия трекер-задачи в
`test_routes_video_tracker_autoclose.py`.
"""

from app.config import settings
from app.models.learning_video import LearningVideo
from app.models.video_quiz import VideoQuizAnswer, VideoQuizResponse
from app.services.video_quiz import sync_questions

VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"


def _configure_bunny(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)


def _video_with_quiz(db, *, duration_seconds: float = 600.0, questions=("Что было важным?", "Что осталось непонятным?")):
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок с мини-опросом",
        status="ready",
        is_published=True,
        duration_seconds=duration_seconds,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    if questions:
        sync_questions(db, video_id=video.id, items=[(None, q) for q in questions])
        db.commit()
        db.refresh(video)
    return video


def test_quiz_is_hidden_before_video_is_configured_with_questions(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок без опроса",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()

    page = client.get(f"/cabinet/videos/{video.id}")

    assert page.status_code == 200
    assert 'id="video-quiz"' not in page.text


def test_quiz_form_renders_hidden_before_completion(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(db)

    page = client.get(f"/cabinet/videos/{video.id}")

    assert page.status_code == 200
    assert 'id="video-quiz"' in page.text
    assert 'id="video-quiz" class="card" hidden' in page.text
    assert "Что было важным?" in page.text
    assert "Что осталось непонятным?" in page.text


def test_quiz_form_visible_when_video_already_completed(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(db)
    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )

    page = client.get(f"/cabinet/videos/{video.id}")

    assert page.status_code == 200
    assert 'id="video-quiz" class="card" aria-label' in page.text


def test_submit_before_watching_is_rejected(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(db)

    response = client.post(
        f"/cabinet/videos/{video.id}/quiz",
        json={"answers": ["Ответ 1", "Ответ 2"]},
    )

    assert response.status_code == 409
    assert db.query(VideoQuizResponse).count() == 0


def test_submit_saves_answers_after_watching(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(db)
    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )

    response = client.post(
        f"/cabinet/videos/{video.id}/quiz",
        json={"answers": ["Про свет и тень", "Пропорции лица"]},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    saved = (
        db.query(VideoQuizResponse)
        .filter(VideoQuizResponse.video_id == video.id, VideoQuizResponse.user_id == user.id)
        .one()
    )
    answers = {
        answer.question_id: answer.text
        for answer in db.query(VideoQuizAnswer)
        .filter(VideoQuizAnswer.response_id == saved.id)
        .all()
    }
    question_ids = [q.id for q in video.questions]
    assert answers[question_ids[0]] == "Про свет и тень"
    assert answers[question_ids[1]] == "Пропорции лица"


def test_submit_supports_more_than_three_questions(auth_client, db, monkeypatch):
    """Ровно то отличие от старой реализации, ради которого делали
    перенормализацию 29.08.2026 — раньше упёрлись бы в третье фиксированное
    поле."""
    client, user = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(
        db, questions=("Вопрос 1", "Вопрос 2", "Вопрос 3", "Вопрос 4", "Вопрос 5")
    )
    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )

    response = client.post(
        f"/cabinet/videos/{video.id}/quiz",
        json={"answers": ["1", "2", "3", "4", "5"]},
    )

    assert response.status_code == 200
    saved = (
        db.query(VideoQuizResponse)
        .filter(VideoQuizResponse.video_id == video.id, VideoQuizResponse.user_id == user.id)
        .one()
    )
    assert (
        db.query(VideoQuizAnswer).filter(VideoQuizAnswer.response_id == saved.id).count() == 5
    )


def test_submit_wrong_answer_count_is_rejected(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(db)
    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )

    response = client.post(
        f"/cabinet/videos/{video.id}/quiz",
        json={"answers": ["Только один ответ"]},
    )

    assert response.status_code == 422


def test_submit_without_configured_questions_is_not_found(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок без опроса",
        status="ready",
        is_published=True,
        duration_seconds=600.0,
    )
    db.add(video)
    db.commit()
    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )

    response = client.post(
        f"/cabinet/videos/{video.id}/quiz",
        json={"answers": ["Ответ"]},
    )

    assert response.status_code == 404


def test_already_answered_quiz_renders_read_only_and_hides_form(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(db)
    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )
    client.post(
        f"/cabinet/videos/{video.id}/quiz",
        json={"answers": ["Свет", "Тень"]},
    )

    page = client.get(f"/cabinet/videos/{video.id}")

    assert page.status_code == 200
    assert 'id="video-quiz-form"' not in page.text
    assert "Свет" in page.text
    assert "Тень" in page.text


def test_quiz_submit_requires_real_csrf(auth_client, db, monkeypatch):
    from app.csrf import generate_csrf_token
    from app.dependencies import require_csrf_header
    from app.main import app

    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = _video_with_quiz(db)
    client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 598, "duration_seconds": 600},
    )

    csrf_override = app.dependency_overrides.pop(require_csrf_header)
    try:
        session_ids = {
            cookie.value for cookie in client.cookies.jar
            if cookie.name == "session_id"
        }
        assert len(session_ids) == 1
        session_id = session_ids.pop()

        missing = client.post(
            f"/cabinet/videos/{video.id}/quiz",
            json={"answers": ["Ответ 1", "Ответ 2"]},
        )
        valid = client.post(
            f"/cabinet/videos/{video.id}/quiz",
            json={"answers": ["Ответ 1", "Ответ 2"]},
            headers={"X-CSRF-Token": generate_csrf_token(session_id)},
        )

        assert missing.status_code == 403
        assert valid.status_code == 200
    finally:
        app.dependency_overrides[require_csrf_header] = csrf_override
