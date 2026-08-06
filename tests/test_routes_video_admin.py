from app.config import settings
from app.models.learning_video import LearningVideo


VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"


def _configure_upload(monkeypatch):
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_api_key", "test-stream-api-key")


def _rank4_client(client, user_factory, session_factory):
    user = user_factory(
        vk_id=400_004,
        name="Admin",
        is_admin=True,
        is_group_member=False,
        role_name="админ",
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def test_video_admin_section_is_shared_by_admin_and_superadmin(
    client, user_factory, session_factory, admin_client, monkeypatch
):
    _configure_upload(monkeypatch)
    _rank4_client(client, user_factory, session_factory)
    rank4 = client.get("/cabinet/admin/videos")
    assert rank4.status_code == 200
    assert "Загрузить новое видео" in rank4.text
    assert "tus-js-client@4.3.1" in rank4.text
    assert "apparchi:bunny-upload:" in rank4.text

    super_client, _ = admin_client
    rank5 = super_client.get("/cabinet/admin/videos")
    assert rank5.status_code == 200


def test_student_cannot_open_video_admin(auth_client):
    client, _ = auth_client
    assert client.get("/cabinet/admin/videos").status_code == 403


def test_staff_catalogue_uses_only_staff_navigation(admin_client, db, monkeypatch):
    _configure_upload(monkeypatch)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок для предпросмотра",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    client, _ = admin_client

    catalogue = client.get("/cabinet/videos")
    preview = client.get(f"/cabinet/videos/{video.id}")

    assert 'class="staff-pill-nav"' in catalogue.text
    assert 'class="bottom-nav"' not in catalogue.text
    assert 'class="bottom-nav"' not in preview.text


def test_admin_can_create_direct_tus_upload(
    client, db, user_factory, session_factory, monkeypatch
):
    _configure_upload(monkeypatch)
    admin = _rank4_client(client, user_factory, session_factory)
    monkeypatch.setattr(
        "app.api.video_admin.create_video",
        lambda title: {"guid": VIDEO_ID, "status": 0, "encodeProgress": 0},
    )

    response = client.post(
        "/cabinet/admin/videos/create-upload",
        json={
            "title": "Большой урок",
            "description": "Описание",
            "filename": "lesson.mp4",
            "size_bytes": 1_500_000_000,
            "mime_type": "video/mp4",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["endpoint"] == "https://video.bunnycdn.com/tusupload"
    assert data["video_id"] == VIDEO_ID
    assert "test-stream-api-key" not in response.text
    video = db.query(LearningVideo).filter_by(bunny_video_id=VIDEO_ID).one()
    assert video.created_by_id == admin.id
    assert video.status == "uploading"


def test_uncertain_provider_create_tells_admin_not_to_retry(
    client, user_factory, session_factory, monkeypatch
):
    from app.services.bunny_stream import BunnyStreamCreateUncertainError

    _configure_upload(monkeypatch)
    _rank4_client(client, user_factory, session_factory)
    monkeypatch.setattr(
        "app.api.video_admin.create_video",
        lambda title: (_ for _ in ()).throw(BunnyStreamCreateUncertainError("unknown")),
    )

    response = client.post(
        "/cabinet/admin/videos/create-upload",
        json={
            "title": "Неопределённый результат",
            "filename": "lesson.mp4",
            "size_bytes": 100,
            "mime_type": "video/mp4",
        },
    )

    assert response.status_code == 504
    assert response.json()["error"] == "provider_create_uncertain"


def test_rank4_cannot_delete_video(client, db, user_factory, session_factory, monkeypatch):
    _configure_upload(monkeypatch)
    _rank4_client(client, user_factory, session_factory)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Удалить меня",
        status="ready",
        is_published=False,
    )
    db.add(video)
    db.commit()

    response = client.post(
        f"/cabinet/admin/videos/{video.id}/delete",
        json={"confirmation": video.title},
    )
    assert response.status_code == 403


def test_upload_credentials_can_be_renewed_for_same_video(
    client, db, user_factory, session_factory, monkeypatch
):
    _configure_upload(monkeypatch)
    _rank4_client(client, user_factory, session_factory)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Продолжить загрузку",
        status="uploading",
        is_published=False,
    )
    db.add(video)
    db.commit()

    response = client.post(
        f"/cabinet/admin/videos/{video.id}/upload-credentials", json={}
    )

    assert response.status_code == 200
    assert response.json()["video_id"] == VIDEO_ID
    assert "test-stream-api-key" not in response.text


def test_upload_credentials_fail_closed_when_stream_disabled(
    client, db, user_factory, session_factory, monkeypatch
):
    _configure_upload(monkeypatch)
    _rank4_client(client, user_factory, session_factory)
    monkeypatch.setattr(settings, "bunny_stream_enabled", False)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Пауза",
        status="uploading",
        is_published=False,
    )
    db.add(video)
    db.commit()

    response = client.post(
        f"/cabinet/admin/videos/{video.id}/upload-credentials", json={}
    )

    assert response.status_code == 503
    assert response.json()["error"] == "upload_not_configured"


def test_admin_upload_uses_catalog_scoped_tus_fingerprint(admin_client):
    client, _ = admin_client

    response = client.get("/cabinet/admin/videos")

    assert response.status_code == 200
    assert "fingerprint: function (selectedFile)" in response.text
    assert "credentials.catalog_video_id" in response.text
    assert "error.status === 404 || error.status === 409" in response.text


def test_superadmin_delete_requires_unpublish_and_exact_title(admin_client, db, monkeypatch):
    _configure_upload(monkeypatch)
    client, _ = admin_client
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Удалить меня",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    monkeypatch.setattr("app.api.video_admin.delete_video", lambda video_id: None)

    still_public = client.post(
        f"/cabinet/admin/videos/{video.id}/delete",
        json={"confirmation": video.title},
    )
    assert still_public.status_code == 409

    video.is_published = False
    db.commit()
    wrong_title = client.post(
        f"/cabinet/admin/videos/{video.id}/delete",
        json={"confirmation": "не то название"},
    )
    assert wrong_title.status_code == 422

    deleted = client.post(
        f"/cabinet/admin/videos/{video.id}/delete",
        json={"confirmation": video.title},
    )
    assert deleted.status_code == 200
    db.refresh(video)
    assert video.status == "deleted"
    assert video.deleted_at is not None


def test_refresh_normalizes_bunny_status(admin_client, db, monkeypatch):
    _configure_upload(monkeypatch)
    client, _ = admin_client
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Обработка",
        status="uploading",
        is_published=False,
    )
    db.add(video)
    db.commit()
    monkeypatch.setattr(
        "app.api.video_admin.get_video",
        lambda video_id: {"status": 3, "encodeProgress": 100, "length": 615},
    )

    response = client.post(f"/cabinet/admin/videos/{video.id}/refresh", json={})

    assert response.status_code == 200
    assert response.json()["video"]["status"] == "ready"
    db.refresh(video)
    assert video.duration_seconds == 615


def test_publish_and_student_catalogue_use_local_source_of_truth(
    admin_client, db, monkeypatch
):
    _configure_upload(monkeypatch)
    client, admin = admin_client
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Опубликованный урок",
        status="ready",
        is_published=False,
    )
    db.add(video)
    db.commit()

    published = client.post(f"/cabinet/admin/videos/{video.id}/publish", json={})
    assert published.status_code == 200
    assert db.get(LearningVideo, video.id).is_published is True

    catalogue = client.get("/cabinet/videos")
    assert catalogue.status_code == 200
    assert "Опубликованный урок" in catalogue.text

    hidden = client.post(f"/cabinet/admin/videos/{video.id}/unpublish", json={})
    assert hidden.status_code == 200
    catalogue = client.get("/cabinet/videos")
    assert "Опубликованный урок" not in catalogue.text


def test_admin_creates_topic_and_page_renders_it(admin_client, db, monkeypatch):
    """Заодно покрывает ветку шаблона со списком тем: там форматируется opens_at."""
    client, _ = admin_client
    _configure_upload(monkeypatch)
    created = client.post(
        "/cabinet/admin/videos/topics",
        json={
            "title": "Архитектура США",
            "opens_at": "2026-08-03T10:00",
            "assign_to_all": True,
        },
    )
    assert created.status_code == 200
    topic_id = created.json()["topic_id"]

    page = client.get("/cabinet/admin/videos")
    assert page.status_code == 200
    assert "Архитектура США" in page.text
    assert "Черновик" in page.text

    published = client.post(f"/cabinet/admin/videos/topics/{topic_id}/publish", json={})
    assert published.status_code == 200
    assert "Опубликована" in client.get("/cabinet/admin/videos").text


def test_topic_with_attached_lesson_cannot_be_deleted(admin_client, db, monkeypatch):
    """Иначе урок молча стал бы доступен всем — удаление темы не должно раздавать контент."""
    client, _ = admin_client
    _configure_upload(monkeypatch)
    created = client.post(
        "/cabinet/admin/videos/topics",
        json={"title": "Тема с уроком", "opens_at": "2026-08-03T10:00", "assign_to_all": True},
    )
    topic_id = created.json()["topic_id"]

    db.add(LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок темы",
        status="ready",
        is_published=True,
        topic_id=topic_id,
    ))
    db.commit()

    refused = client.post(f"/cabinet/admin/videos/topics/{topic_id}/delete", json={})
    assert refused.status_code == 409
    assert refused.json()["error"] == "topic_has_videos"


def test_student_cannot_manage_topics(auth_client):
    client, _ = auth_client
    response = client.post(
        "/cabinet/admin/videos/topics",
        json={"title": "Чужая тема", "opens_at": "2026-08-03T10:00", "assign_to_all": True},
    )
    assert response.status_code == 403
