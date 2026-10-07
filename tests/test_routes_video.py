"""Route tests for the protected Bunny Stream pilot page."""

from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.models.learning_video import LearningVideo


VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"
TOKEN_KEY = "route-private-test-key"


def seed_video_watch(db, *, user_id, video_id=VIDEO_ID, position, covered,
                     duration=None, session=""):
    """Ученик уже посмотрел ролик с начала до `covered` (07.10.2026: зачёт
    считает покрытие по отрезкам сеансов). Сеанс по умолчанию — общий, как у
    отметок без `session_id`, которые шлют тесты роутов."""
    from app.models.video_progress import VideoProgress
    from app.models.video_watch_segment import VideoWatchSegment

    db.add(VideoProgress(
        user_id=user_id, video_id=video_id, position_seconds=position,
        watched_seconds=covered, covered_seconds=covered, duration_seconds=duration,
    ))
    db.add(VideoWatchSegment(
        user_id=user_id, video_id=video_id, session_id=session,
        start_seconds=0.0, end_seconds=covered,
    ))
    db.commit()


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
    # Телефон в ватермарке больше не показываем (владелец 01.09.2026): зрителя
    # опознают имя и username, а номер только утяжелял надпись.
    assert "+7 999 123-45-67" not in response.text
    assert 'class="video-watermark" aria-hidden="true"' in response.text


def test_video_watermark_escapes_viewer_identity(auth_client, db, monkeypatch):
    """Данные зрителя уходят в разметку JSON-блоком (`player_data`, собран
    `htmlsafe_json_dumps` в `app/api/video.py`), а не подстановкой в HTML —
    экранирование там своё: `<`/`>` становятся `\\u003c`/`\\u003e`, а не
    `&lt;`/`&gt;` (см. `_render_player`). Разрыв `<script>` и XSS исключены тем
    же способом, что раньше давал Jinja-автоэкранинг, только другим синтаксисом.
    """
    client, user = auth_client
    _configure_bunny(monkeypatch)
    user.name = '<img src=x onerror="alert(1)">'
    user.tg_username = '<script>alert(2)</script>'
    db.commit()

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    assert "<img src=x" not in response.text
    assert "<script>alert(2)</script>" not in response.text
    assert "\\u003cimg src=x" in response.text
    assert "@\\u003cscript\\u003ealert(2)\\u003c/script\\u003e" in response.text


def test_video_watermark_fades_in_and_out_at_random_spots(auth_client, monkeypatch, served):
    """Надпись со зрителем не летает по кругу, а проявляется и гаснет в
    случайной точке безопасной зоны кадра (владелец 01.09.2026)."""
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    page = served(response)
    assert "function measureWatermarkBounds()" in page
    assert "function pickWatermarkSpot()" in page
    assert "function runWatermarkCycle()" in page
    assert "Math.random()" in page
    assert "watermarkCopy.classList.add('is-visible')" in page
    assert "watermarkCopy.classList.remove('is-visible')" in page
    assert "safeCorners" in page
    assert "Math.random() * 9" not in page
    assert "coverFallbackTimer = window.setTimeout(hideCover, 5000)" in page
    assert 'data-role="cover-play"' in page
    assert "coverPlay.hidden = false" in page
    # Автозапуска нет совсем (владелец 21.09.2026): кнопка «Смотреть» только
    # убирает обложку, дальше зритель жмёт play внутри самого плеера. Прежняя
    # попытка запускать видео за него упиралась в запрет iOS и показывала
    # красное «Видео не запустилось само» вместо картинки.
    assert "Видео не запустилось само" not in page
    assert "playThroughBunny" not in page
    assert "armPlaybackAttemptTimer" not in page
    assert "searchParams.set('autoplay', 'true')" not in page
    assert "searchParams.set('muted', 'true')" not in page
    assert "playRequested" not in page
    assert "window.matchMedia('(pointer: coarse)').matches" not in page
    # Кнопка звука жила только ради беззвучного автозапуска.
    assert 'data-role="mute-btn"' not in page
    assert "data.player_url = body.player_url" in page
    assert "player.on('play'" in page
    assert "hideCover();" in page
    assert "saveProgress(true, false, false);" in page
    # Стили обложки с 27.09.2026 живут в video.css (храповик переиспользования).
    assert "/static/css/video.css?v=" in page
    video_css = (Path(__file__).resolve().parents[1] / "app/static/css/video.css").read_text(encoding="utf-8")
    assert "video-frame.is-started .video-cover" in video_css
    # Слой «Загружаем видео» обязан пропускать касания: автозапуска нет, видео
    # стартует тапом внутри плеера, а этот слой лежит поверх него во весь размер.
    assert "font-size: 13px; pointer-events: none;" in page
    # Разрешение `autoplay` в iframe оставлено сознательно: программного play()
    # у нас больше нет (21.09.2026), но плеер Bunny внутри сам решает, что делать
    # после тапа зрителя, и урезать ему права смысла нет. Автостарт при этом
    # выключен в подписанном URL параметром autoplay=false.
    assert 'allow="accelerometer; gyroscope; autoplay; encrypted-media"' in page
    assert "new ResizeObserver(measureWatermarkBounds)" in page
    assert "(prefers-reduced-motion: reduce)" in page
    assert "bottomPadding" in page
    # Перелёта между точками быть не должно: анимируется только прозрачность,
    # новая координата ставится, пока надпись уже не видна.
    assert "transition: opacity 900ms" in page
    assert "Math.cos(angle)" not in page
    # Прозрачная и без тени под текстом (владелец 01.09.2026).
    assert "color: rgba(255, 255, 255, .22)" in page
    assert "text-shadow" not in page


def test_video_fullscreen_keeps_watermark_inside_fullscreen_container(auth_client, monkeypatch, served):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    page = served(response)
    assert 'data-role="fullscreen-btn"' in page
    assert ".video-frame:fullscreen" in page
    assert "playerContainer.requestFullscreen" in page
    assert "requestFullscreen.call(playerContainer)" in page
    assert "document.exitFullscreen" in page
    assert "document.addEventListener('fullscreenchange'" in page
    assert 'allow="accelerometer; gyroscope; autoplay; encrypted-media"' in page
    assert "allowfullscreen" not in page.lower()
    # Ни фуллскрина, ни картинки-в-картинке у iframe: оба режима выносят кадр
    # из-под слоя с данными зрителя, и видео поехало бы дальше без ватермарки.
    assert "picture-in-picture" not in page


def test_fullscreen_button_sits_top_right_and_video_can_rotate(auth_client, monkeypatch, served):
    """Владелец 05.10.2026: на телефоне и планшете ⛶ внизу справа закрывала
    шестерёнку настроек Bunny, плюс просьба повернуть видео горизонтально."""
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    page = served(client.get("/cabinet/video"))
    # Кнопка вверху: низ кадра занимает панель управления Bunny.
    assert ".video-fullscreen-button {\n    position: absolute;\n    right: 8px;\n    top: 8px;" in page
    assert "bottom: max(8px, env(safe-area-inset-bottom));" not in page
    # Поворот — та же рамка, вместе с водяным знаком, а не системный разворот.
    assert "rotateButton.setAttribute('data-role', 'rotate-btn')" in page
    assert "'rotate(90deg) translateY(-100%)'" in page
    assert "rotated = rotateRequested && height > width;" in page

    css_dir = Path(__file__).resolve().parents[1] / "app" / "static" / "css"
    video_css = (css_dir / "video.css").read_text(encoding="utf-8")
    assert ".video-frame .video-rotate-button[hidden] { display: none; }" in video_css
    tracker_css = (css_dir / "tracker.css").read_text(encoding="utf-8")
    assert "position: absolute; right: 8px; top: 8px; z-index: 4;" in tracker_css
    assert ".lrn-inline-video .video-rotate-button[hidden] { display: none; }" in tracker_css


def test_mobile_video_uses_pseudo_fullscreen_with_watermark(auth_client, monkeypatch, served):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    page = served(response)
    assert ".video-frame.is-pseudo-fullscreen" in page
    assert "height: 100dvh" in page
    assert "safe-area-inset-right" in page
    assert "function shouldUsePseudoFullscreen()" in page
    assert "(max-width: 900px), (pointer: coarse)" in page
    assert "enterPseudoFullscreen()" in page
    assert "exitPseudoFullscreen()" in page
    assert "requestResult.catch(enterPseudoFullscreen)" in page
    assert "fullscreenButton.hidden = true" not in page
    assert "playsinline=true" in page
    assert "disableIosPlayer=true" in page
    assert 'id="video-ios-install-hint"' in page
    assert "function isIosDevice()" in page
    assert "function isStandaloneApp()" in page
    assert "На экран „Домой“" in page


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
    assert saved.json() == {"ok": True, "completed": False, "skipped": False}

    progress = db.get(VideoProgress, (user.id, VIDEO_ID))
    assert progress.position_seconds == 123.5

    page = client.get("/cabinet/video")
    assert page.status_code == 200
    assert '"resume_position_seconds": 123.5' in page.text


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


def test_video_progress_ignores_client_completed_flag(auth_client, db, monkeypatch):
    """Клиентский флаг `completed` отвергается схемой — решает сервер.

    Тест намеренно не обещает большего: это легаси-маршрут пилотного ролика, у
    которого нет записи в каталоге, а значит и своей длительности. Сравнение
    идёт с присланной, и объявить пилот пройденным клиент технически может.
    Для уроков каталога дыра закрыта серверной длительностью — см.
    `test_completion_uses_server_duration_not_client_claim`.
    """
    from app.models.video_progress import VideoProgress

    client, user = auth_client
    _configure_bunny(monkeypatch)

    forged = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 20, "duration_seconds": 100, "completed": True},
    )
    # Защита от перемотки (владелец 05.09.2026) требует просмотренного куска
    # ролика — симулируем, что ученик уже почти досмотрел, иначе один
    # heartbeat у конца ролика просмотр не засчитывает.
    seed_video_watch(db, user_id=user.id, position=90.0, covered=90.0)
    near_end = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 98, "duration_seconds": 100},
    )

    assert forged.status_code == 422
    assert near_end.status_code == 200
    assert near_end.json() == {"ok": True, "completed": True, "skipped": False}
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
    assert '"resume_position_seconds": 0.0' in response.text


def test_video_progress_save_failure_returns_safe_503(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    def fail_save(*args, **kwargs):
        raise SQLAlchemyError("test failure")

    monkeypatch.setattr("app.api.video.record_watch", fail_save)
    response = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 30, "duration_seconds": 100},
    )

    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "save_failed"}


def test_player_url_endpoint_mints_a_fresh_signed_link(auth_client, db, monkeypatch):
    """Подписанная ссылка живёт минуты, страница — часами.

    Без свежего URL любой перезапрос iframe (возврат на вкладку, перезапуск PWA)
    упирается в заглушку Bunny.
    """
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок с темой",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()

    page = client.get(f"/cabinet/videos/{video.id}")
    first = client.get(f"/cabinet/videos/{video.id}/player-url")

    assert page.status_code == 200
    assert f'"/cabinet/videos/{video.id}/player-url"' in page.text
    assert first.status_code == 200
    payload = first.json()
    assert payload["ok"] is True
    assert f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}" in payload["player_url"]
    assert "token=" in payload["player_url"]
    assert payload["ttl_seconds"] == 300
    assert TOKEN_KEY not in first.text


def test_player_url_endpoint_respects_topic_access(auth_client, db, admin_user, monkeypatch):
    """Тот же фильтр, что у страницы: ссылка не должна обходить темы."""
    from datetime import timedelta

    from app.models.learning_topic import LearningTopic, LearningTopicTag
    from app.models.tag import Tag
    from app.services.tz import now_msk

    client, _ = auth_client
    _configure_bunny(monkeypatch)
    topic = LearningTopic(
        title="Чужая тема",
        opens_at=now_msk() - timedelta(days=1),
        is_published=True,
        created_by_id=admin_user.id,
    )
    tag = Tag(name="Чужой поток")
    db.add_all([topic, tag])
    db.flush()
    db.add(LearningTopicTag(topic_id=topic.id, tag_id=tag.id))
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок чужой темы",
        status="ready",
        is_published=True,
        topic_id=topic.id,
    )
    db.add(video)
    db.commit()

    response = client.get(f"/cabinet/videos/{video.id}/player-url")

    assert response.status_code == 404
    assert "iframe.mediadelivery.net" not in response.text


def test_player_url_endpoint_fails_closed_without_configuration(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch, token_key="")

    response = client.get("/cabinet/video/player-url")

    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "player_unavailable"}


def test_legacy_player_url_disappears_once_catalogue_exists(auth_client, db, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    pilot = client.get("/cabinet/video/player-url")
    db.add(
        LearningVideo(
            bunny_library_id=720058,
            bunny_video_id=VIDEO_ID,
            title="Первый каталожный урок",
            status="ready",
            is_published=True,
        )
    )
    db.commit()
    after_catalogue = client.get("/cabinet/video/player-url")

    assert pilot.status_code == 200
    assert after_catalogue.status_code == 404


def test_video_page_refreshes_expired_player_url(auth_client, monkeypatch, served):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    page = served(response)
    assert '"player_url_endpoint": "/cabinet/video/player-url"' in page
    assert '"player_url_ttl_seconds": 300' in page
    assert "function isPlayerUrlStale()" in page
    assert "function refreshPlayerUrl()" in page
    assert "iframe.src = data.player_url;" in page
    assert "if (reattachPlayer) reattachPlayer();" in page
    assert "resumeSeconds = currentSeconds;" in page
    assert "if (document.visibilityState === 'visible') refreshPlayerUrlIfStale();" in page
    assert "if (!event.persisted) return;" in page
    # Возврат из bfcache: pagehide гасит цикл ватермарки, и без перезапуска она
    # осталась бы висеть в одной точке — то есть перестала бы мешать записи экрана.
    # Общий плеер (`_video_player.html`) — не завязываемся на точные отступы строк.
    assert "window.addEventListener('pageshow'" in page
    assert "startWatermarkDrift();" in page
    assert "refreshPlayerUrlIfStale();" in page
    # Обвязка пересоздаётся, а старая замолкает по поколению: иначе прогресс
    # сохранялся бы дважды после каждого обновления ссылки.
    assert "function attachPlayer()" in page
    assert "var generation = ++playerGeneration;" in page
    assert "if (generation !== playerGeneration) return;" in page


def test_video_page_has_throttled_playerjs_progress_contract(auth_client, monkeypatch, served):
    client, _ = auth_client
    _configure_bunny(monkeypatch)

    response = client.get("/cabinet/video")

    assert response.status_code == 200
    page = served(response)
    assert "player-0.1.0.min.js" in page
    # Скрипт грузится динамически общим плеером (`loadPlayerJs()` в
    # `_video_player.html`), не статичным тегом `<script src=... integrity=...>` —
    # хэш ставится JS-присваиванием, а не HTML-атрибутом.
    assert "sha384-FzNVGZdy6ImmE/3LFewUFSxAVlmjM0wP4aKlUJYalPvzGkIEva94s2WZgmeQPVvC" in page
    assert "player.on('ready'" in page
    assert "player.on('timeupdate'" in page
    assert "player.on('pause'" in page
    assert "player.on('seeked'" in page
    assert "player.on('ended'" in page
    assert "player.setCurrentTime(resumeSeconds)" in page
    assert "if (resumeSeconds >= 5) {" in page
    assert "Date.now() - lastAutomaticSaveAt >= 10000" in page
    # Ключ ставит общий хелпер (`static/js/csrf.js`), а не сам плеер: с
    # 26.09.2026 прогресс шлётся свежим токеном. Прежний сторож ждал строку
    # `'X-CSRF-Token': csrfToken` в разметке — она означала обратное, что
    # плеер шлёт ключ, вшитый в страницу при отрисовке. За 20 часов до правки
    # именно такой ключ протухал и давал 139 отказов 403 на прогрессе, после
    # чего плеер выключал сохранение до перезагрузки страницы.
    assert "window.csrfFetch" in page
    assert "sendProgressRequest(data.progress_endpoint" in page
    assert "keepalive: Boolean(keepalive)" in page
    assert "if (saveInFlight)" in page
    assert "body: JSON.stringify({" in page
    assert "typeof options.onCompleted === 'function'" in page
    assert "if (!completionReported && onCompleted) onCompleted();" in page


def test_done_step_stays_open_while_its_video_runs():
    """Полный экран держит сделанный шаг АОП раскрытым.

    До 01.10.2026 тело сделанного шага было видно только под курсором
    (`:hover`). Полноэкранная рамка уходит в верхний слой, курсор формально
    покидает карточку, тело получает `display: none`, и полный экран на ПК
    показывал страницу вместо ролика — у владельца 21.09.2026 рамка в полном
    экране была 0x0. Теперь тело раскрывает кнопка «Показать» (аудит
    30.09.2026, находка 5), правила полного экрана остались страховкой.
    Правило «пока ролик запущен» (`.is-started`) снято: с кнопкой оно не
    давало свернуть шаг после просмотра.
    """
    import re
    from pathlib import Path

    css = Path("app/static/css/tracker.css").read_text(encoding="utf-8")

    assert ".lrn-step--done .lrn-step-body { display: none; }" in css
    assert ".lrn-step--done.is-open .lrn-step-body { display: block; }" in css
    assert ":has(.video-frame.is-started) .lrn-step-body" not in css
    assert ".lrn-step--done:has(.video-frame:fullscreen) .lrn-step-body { display: block; }" in css
    assert ".lrn-step--done:has(.video-frame:-webkit-full-screen) .lrn-step-body { display: block; }" in css
    assert ".lrn-step--done:has(.video-frame.is-pseudo-fullscreen) .lrn-step-body { display: block; }" in css
    # Псевдо-полный экран на телефоне — `position: fixed` внутри страницы: любая
    # `opacity` у предка завела бы свой контекст наложения и погасила видео. До
    # 01.10.2026 это лечил обход `body.has-video-pseudo-fullscreen .lrn-step
    # { opacity: 1 !important }`; теперь сделанный шаг приглушён цветом заголовка,
    # а прозрачности у него нет вовсе.
    assert not re.search(r"\.lrn-step--done\s*\{[^}]*opacity", css)
    # Разнесены по одному: неизвестный браузеру селектор в группе через запятую
    # выбрасывает всю группу, и открытие шага пропало бы целиком.
    assert ":has(.video-frame:fullscreen),\n" not in css


def test_video_progress_reports_skip_forward(auth_client, db, monkeypatch, caplog):
    """Перемотка вперёд — ответ несёт `skipped`, плеер предупреждает ученика
    (владелец 06.10.2026)."""
    from app.models.video_progress import VideoProgress

    client, user = auth_client
    _configure_bunny(monkeypatch)
    seed_video_watch(db, user_id=user.id, position=10.0, covered=10.0)

    with caplog.at_level("WARNING", logger="app.api.video"):
        jumped = client.post(
            "/cabinet/video/progress",
            json={"position_seconds": 300, "duration_seconds": 600, "playback_active": True},
        )

    assert jumped.status_code == 200
    assert jumped.json() == {"ok": True, "completed": False, "skipped": True}
    assert db.get(VideoProgress, (user.id, VIDEO_ID)).watched_seconds < 20
    # Срезанный кусок виден в логе: откуда куда, сколько срезано, играло ли.
    line = next(r.getMessage() for r in caplog.records if "кусок не засчитан" in r.getMessage())
    assert f"user={user.id}" in line
    assert "позиция 10→300" in line
    assert "играло=True" in line


def test_two_players_with_sessions_do_not_cut_each_other(auth_client, db, monkeypatch):
    """Владелец 06.10.2026: две вкладки одного ролика давали «пилу» позиций,
    и каждая отметка отстающей вкладки срезалась. С 07.10.2026 отметки
    сравниваются внутри своего сеанса — срезов нет, покрытие складывается."""
    from app.models.video_progress import VideoProgress
    from app.models.video_watch_event import VideoWatchEvent

    client, user = auth_client
    _configure_bunny(monkeypatch)
    for session, position in [("tab-a", 36), ("tab-b", 100), ("tab-a", 40), ("tab-b", 104)]:
        resp = client.post(
            "/cabinet/video/progress",
            json={"position_seconds": position, "duration_seconds": 600,
                  "playback_active": True, "session_id": session},
        )
        assert resp.json() == {"ok": True, "completed": False, "skipped": False}

    assert db.query(VideoWatchEvent).count() == 0
    assert db.get(VideoProgress, (user.id, VIDEO_ID)).covered_seconds == 8.0


def test_progress_rejects_malformed_session_id(auth_client, monkeypatch):
    client, _ = auth_client
    _configure_bunny(monkeypatch)
    resp = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 10, "duration_seconds": 600, "session_id": "a b<script>"},
    )
    assert resp.status_code == 422
