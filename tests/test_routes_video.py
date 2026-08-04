"""Route tests for the protected Bunny Stream pilot page."""

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.models.learning_video import LearningVideo


VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"
TOKEN_KEY = "route-private-test-key"


def _configure_bunny(monkeypatch, *, enabled: bool = True, token_key: str = TOKEN_KEY) -> None:
    monkeypatch.setattr(settings, "bunny_stream_enabled", enabled)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_video_id", VIDEO_ID)
    monkeypatch.setattr(settings, "bunny_stream_token_key", token_key)
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    monkeypatch.setattr(settings, "bunny_stream_video_title", "Тестовый видеоурок")


def test_video_without_session_redirects_to_login(client, monkeypatch):
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video", follow_redirects=False)

    assert response.status_code == 302
    assert "session_expired" in response.headers["location"]


def test_video_is_hidden_when_pilot_is_disabled(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch, enabled=False)

    response = client.get("/cabinet/video")

    assert response.status_code == 404
    assert "iframe.mediadelivery.net" not in response.text


def test_catalogue_feature_flag_fails_closed_even_with_published_row(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch, enabled=False)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Скрытый аварийным выключателем урок",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()

    catalogue = client.get("/cabinet/videos")
    detail = client.get(f"/cabinet/videos/{video.id}")

    assert catalogue.status_code == 200
    assert video.title not in catalogue.text
    assert detail.status_code == 404


def test_group_member_receives_signed_iframe_without_private_key(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}" in response.text
    assert "token=" in response.text
    assert "expires=" in response.text
    assert TOKEN_KEY not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_video_watermark_shows_current_viewer_identity(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    user.first_name = "Анна"
    user.last_name = "Смирнова"
    user.tg_username = "@anna_art"
    user.phone = "+7 999 123-45-67"
    db.commit()

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert response.text.count("Анна Смирнова") == 1
    assert response.text.count("@anna_art") == 1
    assert response.text.count("+7 999 123-45-67") == 1
    assert 'class="video-watermark" aria-hidden="true"' in response.text


def test_video_watermark_escapes_viewer_identity(auth_client, db, monkeypatch):
    client, user = auth_client
    _configure_bunny(monkeypatch)
    user.name = '<img src=x onerror="alert(1)">'
    user.tg_username = '<script>alert(2)</script>'
    user.phone = '<svg onload="alert(3)">'
    db.commit()

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert "<img src=x" not in response.text
    assert "<script>alert(2)</script>" not in response.text
    assert "<svg onload" not in response.text
    assert "&lt;img src=x" in response.text
    assert "@&lt;script&gt;alert(2)&lt;/script&gt;" in response.text
    assert "&lt;svg onload" in response.text


def test_video_watermark_moves_around_safe_orbit(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert "function measureWatermarkOrbit()" in response.text
    assert "Math.cos(angle)" in response.text
    assert "Math.sin(angle)" in response.text
    assert "orbitDuration = 20000" in response.text
    assert "window.requestAnimationFrame(animateWatermark)" in response.text
    assert "new ResizeObserver(measureWatermarkOrbit)" in response.text
    assert "(prefers-reduced-motion: reduce)" in response.text
    assert "step = (step + 1) % 8" in response.text
    assert "bottomPadding" in response.text


def test_video_fullscreen_keeps_watermark_inside_fullscreen_container(
    auth_client, monkeypatch
):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert 'id="video-fullscreen-button"' in response.text
    assert ".video-frame:fullscreen" in response.text
    assert "playerContainer.requestFullscreen" in response.text
    assert "requestFullscreen.call(playerContainer)" in response.text
    assert "document.exitFullscreen" in response.text
    assert "document.addEventListener('fullscreenchange'" in response.text
    assert 'allow="accelerometer; gyroscope; autoplay; encrypted-media; picture-in-picture"' in response.text
    assert "allowfullscreen" not in response.text.lower()
    assert "picture-in-picture; fullscreen" not in response.text


def test_legacy_url_cannot_bypass_catalogue_unpublish(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Скрытый урок",
        status="ready",
        is_published=False,
    )
    db.add(video)
    db.commit()

    response = client.get("/cabinet/video")

    assert response.status_code == 404
    assert "iframe.mediadelivery.net" not in response.text


def test_student_without_group_membership_is_denied(
    client, user_factory, session_factory, monkeypatch
):
    _configure_bunny(monkeypatch)
    user = user_factory(is_group_member=False)
    session = session_factory(user)
    client.cookies.set("session_id", session.id)

    response = client.get("/cabinet/video", follow_redirects=False)

    assert response.status_code == 403
    assert "iframe.mediadelivery.net" not in response.text


def test_staff_can_preview_video_without_group_membership(
    client, db, user_factory, session_factory, monkeypatch
):
    from app.models.role import Role

    _configure_bunny(monkeypatch)
    user = user_factory(is_group_member=False)
    curator_role = db.query(Role).filter(Role.rank == 2).one()
    user.role_id = curator_role.id
    db.commit()
    session = session_factory(user)
    client.cookies.set("session_id", session.id)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}" in response.text


def test_blocked_student_is_denied(client, user_factory, session_factory, monkeypatch):
    _configure_bunny(monkeypatch)
    user = user_factory(is_active=False)
    session = session_factory(user)
    client.cookies.set("session_id", session.id)

    response = client.get("/cabinet/video", follow_redirects=False)

    assert response.status_code == 403
    assert "iframe.mediadelivery.net" not in response.text


def test_incomplete_bunny_configuration_fails_closed(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch, token_key="")

    response = client.get("/cabinet/video")

    assert response.status_code == 503
    assert "Видео временно недоступно" in response.text
    assert "iframe.mediadelivery.net" not in response.text
    assert TOKEN_KEY not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_dashboard_shows_video_card_only_for_complete_configuration(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    enabled_response = client.get("/cabinet/student")
    assert enabled_response.status_code == 200
    assert 'href="/cabinet/videos"' in enabled_response.text

    monkeypatch.setattr(settings, "bunny_stream_token_key", "")
    disabled_response = client.get("/cabinet/student")
    assert disabled_response.status_code == 200
    assert 'href="/cabinet/videos"' not in disabled_response.text


def test_dashboard_hides_video_card_from_nonmember(
    client, user_factory, session_factory, monkeypatch
):
    _configure_bunny(monkeypatch)
    user = user_factory(is_group_member=False)
    session = session_factory(user)
    client.cookies.set("session_id", session.id)

    response = client.get("/cabinet/student")

    assert response.status_code == 200
    assert 'href="/cabinet/videos"' not in response.text


def test_video_progress_is_saved_and_restored_for_current_user(auth_client, db, monkeypatch):
    from app.models.video_progress import VideoProgress

    client, user = auth_client
    _configure_bunny(monkeypatch)

    saved = client.post(
        "/cabinet/video/progress",
        json={
            "position_seconds": 123.5,
            "duration_seconds": 600.0,
        },
    )
    assert saved.status_code == 200
    assert saved.json() == {"ok": True, "completed": False}

    progress = db.get(VideoProgress, (user.id, VIDEO_ID))
    assert progress.position_seconds == 123.5

    page = client.get("/cabinet/video")
    assert page.status_code == 200
    assert "var resumeSeconds = 123.5;" in page.text


def test_video_progress_cannot_override_user_or_video(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.post(
        "/cabinet/video/progress",
        json={
            "position_seconds": 30,
            "duration_seconds": 100,
            "user_id": 999999,
            "video_id": "a9a2f23a-3dd6-4f93-b74e-31dd47e21fe8",
        },
    )

    assert response.status_code == 422


def test_video_progress_rejects_invalid_timing(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    beyond_duration = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 120, "duration_seconds": 100},
    )
    not_a_number = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": "NaN", "duration_seconds": 100},
    )

    assert beyond_duration.status_code == 422
    assert not_a_number.status_code == 422


def test_video_progress_completion_is_calculated_on_server(auth_client, db, monkeypatch):
    from app.models.video_progress import VideoProgress

    client, user = auth_client
    _configure_bunny(monkeypatch)

    forged = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 20, "duration_seconds": 100, "completed": True},
    )
    near_end = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 98, "duration_seconds": 100},
    )

    assert forged.status_code == 422
    assert near_end.status_code == 200
    assert near_end.json() == {"ok": True, "completed": True}
    assert db.get(VideoProgress, (user.id, VIDEO_ID)).completed_at is not None


def test_video_progress_requires_real_csrf(auth_client, monkeypatch):
    from app.csrf import generate_csrf_token
    from app.dependencies import require_csrf_header
    from app.main import app

    client, _ = auth_client
    _configure_bunny(monkeypatch)
    csrf_override = app.dependency_overrides.pop(require_csrf_header)
    try:
        session_ids = {
            cookie.value for cookie in client.cookies.jar
            if cookie.name == "session_id"
        }
        assert len(session_ids) == 1
        session_id = session_ids.pop()

        missing = client.post(
            "/cabinet/video/progress",
            json={"position_seconds": 30, "duration_seconds": 100},
        )
        valid = client.post(
            "/cabinet/video/progress",
            json={"position_seconds": 30, "duration_seconds": 100},
            headers={"X-CSRF-Token": generate_csrf_token(session_id)},
        )

        assert missing.status_code == 403
        assert valid.status_code == 200
    finally:
        app.dependency_overrides[require_csrf_header] = csrf_override


def test_video_progress_failure_does_not_break_playback(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    def fail_read(*args, **kwargs):
        raise SQLAlchemyError("test failure")

    monkeypatch.setattr("app.api.video.get_video_progress", fail_read)
    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert "iframe.mediadelivery.net" in response.text
    assert "var resumeSeconds = 0.0;" in response.text


def test_video_progress_save_failure_returns_safe_503(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    def fail_save(*args, **kwargs):
        raise SQLAlchemyError("test failure")

    monkeypatch.setattr("app.api.video.persist_video_progress", fail_save)
    response = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 30, "duration_seconds": 100},
    )

    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "save_failed"}


def test_video_page_has_throttled_playerjs_progress_contract(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert "player-0.1.0.min.js" in response.text
    assert "integrity=\"sha384-FzNVGZdy6ImmE/3LFewUFSxAVlmjM0wP4aKlUJYalPvzGkIEva94s2WZgmeQPVvC\"" in response.text
    assert "player.on('ready'" in response.text
    assert "player.on('timeupdate'" in response.text
    assert "player.on('pause'" in response.text
    assert "player.on('seeked'" in response.text
    assert "player.on('ended'" in response.text
    assert "player.setCurrentTime(resumeSeconds)" in response.text
    assert "Date.now() - lastAutomaticSaveAt >= 10000" in response.text
    assert "'X-CSRF-Token': csrfToken" in response.text
    assert "keepalive: Boolean(keepalive)" in response.text
    assert "if (saveInFlight)" in response.text
    assert "body: JSON.stringify({" in response.text
