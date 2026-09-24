"""Страница проверки моста до Bunny — доступ и содержимое.

Она существует затем, чтобы владелец проверил мост на своём устройстве, пока
ученики смотрят видео напрямую. Отсюда два требования, которые легко потерять
при правках: ученик сюда не заходит, и глобальная переменная
`BUNNY_PLAYER_PROXY_BASE` от этой страницы не меняется.
"""

from app.config import settings
from app.models.learning_video import LearningVideo


VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"
BRIDGE = "https://video.assaru.space"
PAGE = "/cabinet/admin/video-bridge-test"


def _configure_bunny(monkeypatch, *, proxy_base: str = ""):
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    monkeypatch.setattr(settings, "bunny_player_proxy_base", proxy_base)


def _published_video(db, title: str = "Урок про мост") -> LearningVideo:
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title=title,
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    return video


def test_student_cannot_open_bridge_test_page(auth_client, monkeypatch):
    _configure_bunny(monkeypatch)
    client, _ = auth_client

    assert client.get(PAGE).status_code == 403


def test_page_shows_both_players_on_one_video(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    video = _published_video(db)
    client, _ = admin_client

    response = client.get(PAGE)

    assert response.status_code == 200
    assert f"{BRIDGE}/embed/720058/{VIDEO_ID}" in response.text
    assert f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}" in response.text
    assert f"{BRIDGE}/__a/playerjs/player-0.1.0.min.js" in response.text
    assert video.title in response.text
    # Ключ подписи в разметку не утекает.
    assert "playback-key" not in response.text


def test_page_does_not_turn_the_bridge_on_for_students(admin_client, db, monkeypatch):
    """Открытие страницы не трогает глобальную настройку.

    Ровно эта ошибка и стоила двух неудачных включений: ученики уезжали на мост
    вместе с проверяющим.
    """
    _configure_bunny(monkeypatch)
    _published_video(db)
    client, _ = admin_client

    client.get(PAGE)

    assert settings.bunny_player_proxy_base == ""


def test_page_warns_when_bridge_is_already_on_for_everyone(admin_client, db, monkeypatch):
    """Мост включён ученикам – предупреждаем, но страница остаётся рабочей.

    Правый плеер собирается с явным пустым адресом, поэтому идёт в Bunny
    напрямую при любой глобальной настройке. Проверяем это здесь же: иначе
    текст предупреждения однажды разъедется с тем, что страница делает.
    """
    _configure_bunny(monkeypatch, proxy_base=BRIDGE)
    _published_video(db)
    client, _ = admin_client

    response = client.get(PAGE)

    assert response.status_code == 200
    assert "Мост включён для всех" in response.text
    assert f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}" in response.text
    assert f"{BRIDGE}/embed/720058/{VIDEO_ID}" in response.text


def test_unknown_bridge_address_is_rejected(admin_client, db, monkeypatch):
    """Адрес уходит в `src` скрипта и iframe — чужой домен туда не пускаем."""
    _configure_bunny(monkeypatch)
    _published_video(db)
    client, _ = admin_client

    response = client.get(PAGE, params={"bridge": "https://evil.example"})

    assert response.status_code == 400


def test_page_opens_without_a_video_to_test_on(admin_client, monkeypatch):
    """Без ролика страница открывается и говорит, чего не хватает.

    Пустой базы на проде не бывает, но проверка связи из шага 1 полезна и без
    ролика — она не должна пропадать вместе с плеерами.
    """
    _configure_bunny(monkeypatch)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "")
    client, _ = admin_client

    response = client.get(PAGE)

    assert response.status_code == 200
    assert "Нет ни одного опубликованного ролика" in response.text
    # Проверка связи из шага 1 остаётся на месте и без ролика.
    assert "Проверить всё подряд" in response.text


def test_video_can_be_chosen_by_id(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    _published_video(db, title="Первый ролик")
    second = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id="1c2f4b60-1111-4528-a700-3f69ec56957d",
        title="Второй ролик",
        status="ready",
        is_published=True,
        sort_order=10,
    )
    db.add(second)
    db.commit()
    client, _ = admin_client

    response = client.get(PAGE, params={"video_id": second.id})

    assert response.status_code == 200
    assert second.bunny_video_id in response.text


def test_staff_dashboard_links_to_the_bridge_test(admin_client, db):
    """Кнопка стоит на главном экране, куда владелец попадает и так.

    Просьба владельца 20.09.2026: адрес из переписки открывается в браузере
    без входа и уводит на этот самый экран, поэтому вход должен быть здесь.
    """
    client, _ = admin_client

    response = client.get("/cabinet/superadmin")

    assert response.status_code == 200
    assert PAGE in response.text
    assert "Проверка моста" in response.text


def test_videos_admin_page_links_to_the_bridge_test(admin_client):
    """Со страницы видео на проверку моста ведёт ссылка.

    Без неё адрес приходится набирать руками, а переход по ссылке из
    переписки открывает браузер без входа и уводит на главный экран –
    на этом владелец и споткнулся 20.09.2026.
    """
    client, _ = admin_client

    response = client.get("/cabinet/admin/videos")

    assert response.status_code == 200
    assert PAGE in response.text


# --- личное зеркало на боевой странице урока (?bridge=1) ---------------------
#
# Появилось 21.09.2026: мост, включённый всем, на странице урока у владельца
# видео не запустил, а на служебной странице то же видео через мост играло.
# Значит проверять надо саму страницу урока, и только своим глазом — отсюда
# параметр, который действует на одного зрителя.


def _catalog_video(db):
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок про мост",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    return video


def test_staff_gets_the_bridge_on_the_lesson_page_with_flag(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    video = _catalog_video(db)
    client, _ = admin_client

    response = client.get(f"/cabinet/videos/{video.id}", params={"bridge": "1"})

    assert response.status_code == 200
    assert f"{BRIDGE}/embed/720058/{VIDEO_ID}" in response.text
    assert f"{BRIDGE}/__a/playerjs/player-0.1.0.min.js" in response.text
    # Перевыпуск ссылки тоже через мост, иначе через пять минут страница уедет
    # на прямой Bunny — там у владельца без VPN видео не идёт.
    assert f"/cabinet/videos/{video.id}/player-url?bridge=1" in response.text


def test_flag_works_for_any_viewer_including_students(auth_client, db, monkeypatch):
    """Флаг работает у любого, кто его дописал, — в том числе у ученика.

    Ограничение по роли пришлось снять 21.09.2026: владелец смотрит из кабинета
    ученика, роль в сессии ученическая, и флаг молча игнорировался. Прятать
    нечего — адрес зеркала задан константой в коде, а видео и подпись те же.
    Заодно ссылку можно выдать ученику, у которого видео не открывается.
    """
    _configure_bunny(monkeypatch)
    video = _catalog_video(db)
    client, _ = auth_client

    response = client.get(f"/cabinet/videos/{video.id}", params={"bridge": "1"})

    assert response.status_code == 200
    assert f"{BRIDGE}/embed/720058/{VIDEO_ID}" in response.text


def test_lesson_page_without_flag_stays_direct_for_staff(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    video = _catalog_video(db)
    client, _ = admin_client

    response = client.get(f"/cabinet/videos/{video.id}")

    assert response.status_code == 200
    assert BRIDGE not in response.text


# `admin_client` и `auth_client` делят один TestClient и перетирают друг другу
# cookie сессии, поэтому staff и ученика проверяем отдельными тестами.


def test_player_url_refresh_honours_the_flag_for_staff(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    video = _catalog_video(db)
    client, _ = admin_client

    response = client.get(f"/cabinet/videos/{video.id}/player-url", params={"bridge": "1"})

    assert response.status_code == 200
    assert response.json()["player_url"].startswith(f"{BRIDGE}/embed/")


def test_player_url_refresh_honours_the_flag_for_students_too(auth_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    video = _catalog_video(db)
    client, _ = auth_client

    response = client.get(f"/cabinet/videos/{video.id}/player-url", params={"bridge": "1"})

    assert response.status_code == 200
    assert response.json()["player_url"].startswith(f"{BRIDGE}/embed/")


def test_lesson_page_stays_direct_without_the_flag(auth_client, db, monkeypatch):
    """Без флага ничего не меняется — это главный инвариант правки."""
    _configure_bunny(monkeypatch)
    video = _catalog_video(db)
    client, _ = auth_client

    response = client.get(f"/cabinet/videos/{video.id}")

    assert response.status_code == 200
    assert BRIDGE not in response.text
    assert f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}" in response.text


def test_impersonated_student_session_accepts_the_flag(client, db, session_factory, admin_user, user_factory, monkeypatch):
    """Вход под учеником: флаг работает, потому что за рулём staff.

    Владелец проверяет мост из кабинета ученика — иначе не увидит ровно то, что
    видит ученик. Признак `impersonated_by_id` есть только у такой сессии.
    """
    _configure_bunny(monkeypatch)
    video = _catalog_video(db)
    student = user_factory(is_group_member=True)
    sess = session_factory(student)
    sess.impersonated_by_id = admin_user.id
    db.commit()
    client.cookies.set("session_id", sess.id)

    response = client.get(f"/cabinet/videos/{video.id}", params={"bridge": "1"})

    assert response.status_code == 200
    assert f"{BRIDGE}/embed/720058/{VIDEO_ID}" in response.text


# --- «Как у ученика, с контролем просмотра» (владелец 24.09.2026) ----------


def _duration_video(db) -> LearningVideo:
    video = _published_video(db)
    video.duration_seconds = 600.0
    db.commit()
    return video


def test_student_view_page_is_the_lesson_page_through_the_bridge(admin_client, db, monkeypatch):
    """Тот же урок, что у ученика: плеер через мост, водяной знак, плюс
    панель контроля. Глобальная настройка при этом не меняется."""
    _configure_bunny(monkeypatch)
    video = _duration_video(db)
    client, _ = admin_client

    response = client.get(f"{PAGE}/player?video_id={video.id}")

    assert response.status_code == 200
    assert f"{BRIDGE}/embed/720058/{VIDEO_ID}" in response.text
    assert 'data-role="watermark"' in response.text
    assert 'data-role="watch-debug"' in response.text
    assert f"/cabinet/videos/{video.id}/player-url?bridge=1" in response.text
    assert settings.bunny_player_proxy_base == ""


def test_student_lesson_page_has_no_watch_panel(auth_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    video = _duration_video(db)
    client, _ = auth_client

    response = client.get(f"/cabinet/videos/{video.id}")

    assert response.status_code == 200
    assert 'data-role="watch-debug"' not in response.text


def test_student_cannot_use_watch_control_routes(auth_client, db, monkeypatch):
    _configure_bunny(monkeypatch)
    video = _duration_video(db)
    client, _ = auth_client

    assert client.get(f"{PAGE}/player?video_id={video.id}").status_code == 403
    assert client.get(f"{PAGE}/watch-state?video_id={video.id}").status_code == 403


def test_watch_state_follows_the_shared_rule(admin_client, db, monkeypatch):
    """Порог за 30 секунд до конца; до первого просмотра ничего не засчитано."""
    _configure_bunny(monkeypatch)
    video = _duration_video(db)
    client, _ = admin_client

    body = client.get(f"{PAGE}/watch-state?video_id={video.id}").json()

    assert body["ok"] is True
    assert body["duration_seconds"] == 600.0
    assert body["threshold_seconds"] == 570.0
    assert body["credited_this_pass"] == 0.0
    assert body["completed"] is False
    assert body["block_id"] is None


def test_reset_clears_only_own_progress(admin_client, db, user_factory, monkeypatch):
    from app.models.video_progress import VideoProgress
    from app.services.video_progress import save_video_progress

    _configure_bunny(monkeypatch)
    video = _duration_video(db)
    client, admin = admin_client
    other = user_factory(vk_id=770_001, name="Ученик")
    for user_id in (admin.id, other.id):
        save_video_progress(
            db, user_id=user_id, video_id=VIDEO_ID, position_seconds=580.0,
            duration_seconds=600.0, completed=True, watched_seconds=580.0,
        )

    response = client.post(
        f"{PAGE}/reset?video_id={video.id}",
        headers={"X-CSRF-Token": client.cookies.get("csrf_token", "")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["completed"] is False
    assert db.get(VideoProgress, (admin.id, VIDEO_ID)) is None
    assert db.get(VideoProgress, (other.id, VIDEO_ID)) is not None
