from datetime import datetime, timezone

from app.config import settings
from app.models.learning_video import LearningVideo
from app.services.video_topics import get_topic


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
        "app.services.video_catalog.get_video",
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


def test_admin_creates_and_publishes_topic(admin_client, db, monkeypatch):
    """Блок «Темы недели» убран со страницы 27.08.2026, маршруты остались.

    Проверяем только их: страница загрузки тем больше не показывает, а
    служебные темы элементов учебной программы заводит календарь.
    """
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

    published = client.post(f"/cabinet/admin/videos/topics/{topic_id}/publish", json={})
    assert published.status_code == 200
    assert get_topic(db, topic_id).is_published is True


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


def test_topic_response_reports_audience_and_ambiguous_tags(
    admin_client, db, user_factory, monkeypatch
):
    """Промах адресации должен быть виден при сохранении, а не через неделю.

    Тег «Р» в проде значит группу и уровень куратора; ученик с «Р+К» под него не
    подпадает, потому что сопоставление строгое.
    """
    from app.models.tag import Tag, UserTag

    client, _ = admin_client
    _configure_upload(monkeypatch)
    exact = user_factory(vk_id=400_010, name="С тегом Р")
    wider = user_factory(vk_id=400_011, name="С тегом Р+К")
    narrow_tag = Tag(name="Р")
    wide_tag = Tag(name="Р+К")
    db.add_all([narrow_tag, wide_tag])
    db.flush()
    db.add_all([
        UserTag(user_id=exact.id, tag_id=narrow_tag.id),
        UserTag(user_id=wider.id, tag_id=wide_tag.id),
    ])
    db.commit()

    created = client.post(
        "/cabinet/admin/videos/topics",
        json={
            "title": "Тема на спорный тег",
            "opens_at": "2026-08-03T10:00",
            "tag_ids": [narrow_tag.id],
        },
    )

    assert created.status_code == 200
    assert created.json()["audience_size"] == 1
    assert created.json()["ambiguous_tags"] == ["Р"]


def test_topic_for_everyone_reports_full_student_audience(
    admin_client, db, user_factory, monkeypatch
):
    client, _ = admin_client
    _configure_upload(monkeypatch)
    user_factory(vk_id=400_020, name="Ученик один")
    user_factory(vk_id=400_021, name="Ученик два")
    user_factory(vk_id=400_022, name="Куратор", role_name="куратор")

    created = client.post(
        "/cabinet/admin/videos/topics",
        json={
            "title": "Тема всем",
            "opens_at": "2026-08-03T10:00",
            "assign_to_all": True,
        },
    )

    assert created.status_code == 200
    assert created.json()["audience_size"] == 2
    assert created.json()["ambiguous_tags"] == []


def test_student_cannot_manage_topics(auth_client):
    client, _ = auth_client
    response = client.post(
        "/cabinet/admin/videos/topics",
        json={"title": "Чужая тема", "opens_at": "2026-08-03T10:00", "assign_to_all": True},
    )
    assert response.status_code == 403


def test_topic_edit_keeps_named_students(admin_client, db, user_factory, monkeypatch):
    """Правка темы не должна снимать доступ у назначенных поимённо.

    Сохранение переписывает список целиком: вызов без `assignee_usernames`
    молча отберёт тему у догоняющих. Форма темы со страницы убрана, но сам
    маршрут остался, и договор о полном составе в запросе действует.
    """
    from app.services.video_topics import get_assignee_ids

    client, _ = admin_client
    _configure_upload(monkeypatch)
    student = user_factory(vk_id=400_030, name="Догоняющий")
    student.tg_username = "catchup"
    db.commit()

    created = client.post(
        "/cabinet/admin/videos/topics",
        json={
            "title": "Тема для догоняющих",
            "opens_at": "2026-08-03T10:00",
            "assignee_usernames": "@catchup",
        },
    )
    topic_id = created.json()["topic_id"]
    assert created.json()["audience_size"] == 1

    renamed = client.post(
        f"/cabinet/admin/videos/topics/{topic_id}",
        json={
            "title": "Тема для догоняющих (переименована)",
            "opens_at": "2026-08-03T10:00",
            "assignee_usernames": "@catchup",
        },
    )

    assert renamed.status_code == 200
    assert renamed.json()["audience_size"] == 1
    assert get_assignee_ids(db, topic_id) == [student.id]


def test_topic_audience_counts_only_group_members(
    admin_client, db, user_factory, monkeypatch
):
    """Ученик без членства в группе получает 403 на весь видеомодуль.

    Счётчик охвата существует ради того, чтобы поймать промах адресации до жалоб;
    показывать в нём людей, которые физически не откроют урок, — обманывать себя.
    """
    client, _ = admin_client
    _configure_upload(monkeypatch)
    user_factory(vk_id=400_040, name="В группе")
    user_factory(vk_id=400_041, name="Вне группы", is_group_member=False)

    created = client.post(
        "/cabinet/admin/videos/topics",
        json={"title": "Тема всем", "opens_at": "2026-08-03T10:00", "assign_to_all": True},
    )

    assert created.status_code == 200
    assert created.json()["audience_size"] == 1


def test_video_topic_change_is_audited(admin_client, db, monkeypatch):
    """Смена темы урока меняет его видимость, поэтому обязана оставлять след.

    Переброс урока на «доступно всем» (`topic_id=None`) — единственный способ
    раздать платный материал одним запросом, и он был единственным мутирующим
    маршрутом файла без записи в аудит.
    """
    from app.models.audit_log import AuditLog

    client, _ = admin_client
    _configure_upload(monkeypatch)
    created = client.post(
        "/cabinet/admin/videos/topics",
        json={"title": "Закрытая тема", "opens_at": "2026-08-03T10:00"},
    )
    topic_id = created.json()["topic_id"]
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок закрытой темы",
        status="ready",
        is_published=True,
        topic_id=topic_id,
    )
    db.add(video)
    db.commit()

    opened = client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={"title": "Урок закрытой темы", "topic_id": None},
    )

    assert opened.status_code == 200
    entry = (
        db.query(AuditLog)
        .filter(AuditLog.action == "video_metadata_update")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert entry is not None
    assert f'"topic_id_before": {topic_id}' in entry.details
    assert '"topic_id_after": null' in entry.details


def test_metadata_without_topic_id_keeps_the_binding(admin_client, db, monkeypatch):
    """Правка названия не должна отвязывать урок от темы.

    Страница загрузки больше не показывает выбор темы и ключ `topic_id` не
    шлёт. Раньше отсутствующий ключ читался как «открыть всем»: ролик учебной
    программы терял и адресность, и автозакрытие задачи в трекере.
    """
    from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, LearningTopic

    client, _ = admin_client
    _configure_upload(monkeypatch)
    topic = LearningTopic(
        title="Видео · Перспектива",
        kind=TOPIC_KIND_PROGRAM_ITEM,
        opens_at=datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
    )
    db.add(topic)
    db.flush()
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id="guid-keep-topic",
        title="Старое название",
        status="ready",
        topic_id=topic.id,
    )
    db.add(video)
    db.commit()

    saved = client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={"title": "Новое название"},
    )

    assert saved.status_code == 200
    db.expire_all()
    video = db.get(LearningVideo, video.id)
    assert video.title == "Новое название"
    assert video.topic_id == topic.id


def test_completion_uses_server_duration_not_client_claim(
    auth_client, db, monkeypatch
):
    """Клиент не должен уметь объявить урок пройденным.

    Валидатор ловит только позицию больше длительности, поэтому тело
    `{"position_seconds": 0, "duration_seconds": 1}` проходило проверку и ставило
    `completed_at` без единой секунды просмотра. Настоящая длительность у сервера
    есть — её пишет `refresh_video_status` из ответа Bunny.
    """
    from app.models.video_progress import VideoProgress

    client, user = auth_client
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок на час",
        status="ready",
        is_published=True,
        duration_seconds=3600.0,
    )
    db.add(video)
    db.commit()

    forged = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 0, "duration_seconds": 1},
    )

    assert forged.status_code == 200
    assert forged.json()["completed"] is False
    assert db.get(VideoProgress, (user.id, VIDEO_ID)).completed_at is None

    honest = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 3598, "duration_seconds": 3600},
    )

    assert honest.json()["completed"] is True
    assert db.get(VideoProgress, (user.id, VIDEO_ID)).completed_at is not None


def test_catalog_progress_requires_real_csrf(auth_client, db, monkeypatch):
    """`conftest` глушит CSRF на всю сессию, поэтому проверяем настоящую зависимость.

    Без такого теста удаление `require_csrf_header` из любого нового маршрута —
    включая публикацию и удаление уроков — не уронило бы ни одной проверки.
    """
    from app.csrf import generate_csrf_token
    from app.dependencies import require_csrf_header
    from app.main import app

    client, _ = auth_client
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок с CSRF",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()

    csrf_override = app.dependency_overrides.pop(require_csrf_header)
    try:
        session_id = next(
            cookie.value for cookie in client.cookies.jar if cookie.name == "session_id"
        )
        missing = client.post(
            f"/cabinet/videos/{video.id}/progress",
            json={"position_seconds": 10, "duration_seconds": 100},
        )
        valid = client.post(
            f"/cabinet/videos/{video.id}/progress",
            json={"position_seconds": 10, "duration_seconds": 100},
            headers={"X-CSRF-Token": generate_csrf_token(session_id)},
        )

        assert missing.status_code == 403
        assert valid.status_code == 200
    finally:
        app.dependency_overrides[require_csrf_header] = csrf_override


def test_topic_and_publish_routes_require_real_csrf(admin_client, db, monkeypatch):
    """Маршруты, раздающие и отбирающие доступ к контенту, обязаны требовать токен."""
    from app.csrf import generate_csrf_token
    from app.dependencies import require_csrf_header
    from app.main import app

    client, _ = admin_client
    _configure_upload(monkeypatch)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок для публикации",
        status="ready",
        is_published=False,
    )
    db.add(video)
    db.commit()

    csrf_override = app.dependency_overrides.pop(require_csrf_header)
    try:
        session_id = next(
            cookie.value for cookie in client.cookies.jar if cookie.name == "session_id"
        )
        token = {"X-CSRF-Token": generate_csrf_token(session_id)}

        assert client.post(
            "/cabinet/admin/videos/topics",
            json={"title": "Без токена", "opens_at": "2026-08-03T10:00", "assign_to_all": True},
        ).status_code == 403
        assert client.post(
            f"/cabinet/admin/videos/{video.id}/publish", json={}
        ).status_code == 403
        assert client.post(
            f"/cabinet/admin/videos/{video.id}/metadata",
            json={"title": "Без токена", "topic_id": None},
        ).status_code == 403

        assert client.post(
            f"/cabinet/admin/videos/{video.id}/publish", json={}, headers=token
        ).status_code == 200
    finally:
        app.dependency_overrides[require_csrf_header] = csrf_override


def test_completion_is_refused_when_server_duration_is_unknown(
    auth_client, db, monkeypatch
):
    """Не знаем длительность — не засчитываем просмотр. Позиция при этом пишется.

    Случай создаёт сама миграция каталога: пилотный ролик вставляется
    опубликованным и без `duration_seconds`, а `publish_video` длительность не
    требует. Пока код откатывался на клиентскую, такой урок отмечался пройденным
    телом `{"position_seconds": 0, "duration_seconds": 1}`.
    """
    from app.models.video_progress import VideoProgress

    client, user = auth_client
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок без известной длительности",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    assert video.duration_seconds is None

    forged = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 0, "duration_seconds": 1},
    )

    assert forged.status_code == 200
    assert forged.json()["completed"] is False
    saved = db.get(VideoProgress, (user.id, VIDEO_ID))
    assert saved.completed_at is None
    assert saved.position_seconds == 0


def test_zero_server_duration_is_not_treated_as_known(auth_client, db, monkeypatch):
    """`length: 0` от Bunny — это «неизвестно», а не «урок нулевой длины».

    Через `or` такое значение откатывалось к клиентскому и открывало ту же дыру.
    """
    from app.models.video_progress import VideoProgress

    client, user = auth_client
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок с нулевой длительностью",
        status="ready",
        is_published=True,
        duration_seconds=0.0,
    )
    db.add(video)
    db.commit()

    response = client.post(
        f"/cabinet/videos/{video.id}/progress",
        json={"position_seconds": 0, "duration_seconds": 1},
    )

    assert response.json()["completed"] is False
    assert db.get(VideoProgress, (user.id, VIDEO_ID)).completed_at is None


def test_metadata_update_creates_reorders_and_clears_quiz_questions(admin_client, db):
    client, user = admin_client
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок",
        status="ready",
    )
    db.add(video)
    db.commit()

    # Пустые строки формы («+» без текста) до сервера не доходят — их
    # отсеивает клиентский JS, а payload-модель требует непустой text.
    filled = client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={
            "title": "Урок",
            "topic_id": None,
            "quiz_questions": [
                {"id": None, "text": "  Что было важным?  "},
                {"id": None, "text": "Как применишь?"},
            ],
        },
    )
    assert filled.status_code == 200
    db.refresh(video)
    assert [q.text for q in video.questions] == ["Что было важным?", "Как применишь?"]

    cleared = client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={"title": "Урок", "topic_id": None, "quiz_questions": []},
    )
    assert cleared.status_code == 200
    db.refresh(video)
    assert video.questions == []


def test_metadata_update_without_quiz_questions_key_leaves_them_untouched(admin_client, db):
    """Ключ `quiz_questions` не прислали — тот же принцип, что у `topic_id`:
    поле не трогаем. Иначе правка одного названия видео молча стирала бы весь
    настроенный мини-опрос вместе с ответами учеников."""
    client, user = admin_client
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок",
        status="ready",
    )
    db.add(video)
    db.commit()
    client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={"title": "Урок", "topic_id": None, "quiz_questions": [{"id": None, "text": "Вопрос"}]},
    )
    db.refresh(video)
    question_id = video.questions[0].id

    response = client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={"title": "Новое название", "topic_id": None},
    )

    assert response.status_code == 200
    db.refresh(video)
    assert video.title == "Новое название"
    assert len(video.questions) == 1
    assert video.questions[0].id == question_id


def test_metadata_update_editing_question_by_id_keeps_student_answers(admin_client, db):
    from app.models.video_quiz import VideoQuizResponse
    from app.services.video_quiz import get_answers_map, get_quiz_question_rows, save_response

    client, user = admin_client
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок",
        status="ready",
    )
    db.add(video)
    db.commit()
    client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={"title": "Урок", "topic_id": None, "quiz_questions": [{"id": None, "text": "Черновой текст"}]},
    )
    db.refresh(video)
    question_id = video.questions[0].id
    save_response(
        db,
        video_id=video.id,
        user_id=user.id,
        question_rows=get_quiz_question_rows(video),
        answers=["Ответ ученика"],
    )
    db.commit()

    response = client.post(
        f"/cabinet/admin/videos/{video.id}/metadata",
        json={
            "title": "Урок",
            "topic_id": None,
            "quiz_questions": [{"id": question_id, "text": "Финальный текст"}],
        },
    )

    assert response.status_code == 200
    db.refresh(video)
    assert len(video.questions) == 1
    assert video.questions[0].id == question_id
    assert video.questions[0].text == "Финальный текст"
    saved = (
        db.query(VideoQuizResponse)
        .filter(VideoQuizResponse.video_id == video.id, VideoQuizResponse.user_id == user.id)
        .one()
    )
    assert get_answers_map(db, response_id=saved.id) == {question_id: "Ответ ученика"}
