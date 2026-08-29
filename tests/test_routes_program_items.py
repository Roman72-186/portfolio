"""Видеоматериал и самостоятельная работа внутри дня программы."""

from datetime import date, datetime

from app.models.homework import HomeworkAssignment, HomeworkImage
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.tag import Tag, UserTag
from app.models.tracker import TrackerTask
from app.services.tz import MSK_TZ
from app.services.video_catalog import is_video_accessible
from app.services.video_topics import list_topics

PROGRAM = "/cabinet/staff/program"
TODAY = date(2026, 8, 21)
MONDAY = "2026-08-24"


def _staff_client(client, user_factory, session_factory, *, vk_id=540_004):
    user = user_factory(
        vk_id=vk_id,
        name="Главный преподаватель",
        is_admin=True,
        is_group_member=False,
        role_name="админ",
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _freeze(monkeypatch, value: date = TODAY):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _video(db) -> LearningVideo:
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id="guid-program-1",
        title="Черновое название",
        status="processing",
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


# ── Видеоматериал ─────────────────────────────────────────────────────────

def test_video_item_binds_topic_cover_and_task(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    student = user_factory(vk_id=541_001, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    video = _video(db)

    response = client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": video.id,
            "title": "Перспектива, часть 1",
            "description": "Смотреть до конца",
            "cover_url": "https://s3.example/cover.jpg",
            "cover_path": "Обложки видео/cover.jpg",
            "subject": "Рисунок",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 200
    assert response.json()["audience_size"] == 1
    db.expire_all()
    video = db.get(LearningVideo, video.id)
    assert video.title == "Перспектива, часть 1"
    assert video.cover_s3_url == "https://s3.example/cover.jpg"
    assert video.topic_id is not None
    topic = db.get(LearningTopic, video.topic_id)
    assert topic.kind == TOPIC_KIND_PROGRAM_ITEM
    task = db.query(TrackerTask).one()
    assert task.kind == "video" and task.topic_id == topic.id


def test_video_item_publishes_immediately_when_already_ready(
    client, db, user_factory, session_factory, monkeypatch
):
    """Постановка в день — это и есть решение «показать ученикам»: второго
    клика «Опубликовать» на отдельной странице требовать не нужно (владелец
    29.08.2026)."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)
    video.status = "ready"
    db.commit()

    response = client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": video.id,
            "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 200
    db.expire_all()
    video = db.get(LearningVideo, video.id)
    assert video.is_published is True
    assert video.published_at is not None


def test_video_item_flags_auto_publish_when_still_processing(
    client, db, user_factory, session_factory, monkeypatch
):
    """Ролик ещё обрабатывается на Bunny в момент постановки в день — публикация
    откладывается до готовности (exam_scheduler.py::_run_video_status_sync),
    но не требует, чтобы куратор вернулся и нажал «Опубликовать» вручную."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)
    assert video.status == "processing"

    response = client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": video.id,
            "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 200
    db.expire_all()
    video = db.get(LearningVideo, video.id)
    assert video.auto_publish_on_ready is True
    assert video.is_published is False


def test_video_picked_from_catalog_keeps_its_own_title_and_cover(
    client, db, user_factory, session_factory, monkeypatch
):
    """Календарь только выбирает ролик: карточку он не переписывает.

    Название и обложку задают на вкладке «Загрузка видео», и форма дня их не
    присылает. Без подстановки из каталога день встал бы в трекер безымянным.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)
    video.title = "Перспектива, часть 1"
    video.cover_s3_url = "https://s3.example/cover.jpg"
    db.commit()

    response = client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": video.id,
            "subject": "Рисунок",
            "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 200
    db.expire_all()
    video = db.get(LearningVideo, video.id)
    assert video.title == "Перспектива, часть 1"
    assert video.cover_s3_url == "https://s3.example/cover.jpg"
    task = db.query(TrackerTask).one()
    assert task.title == "Перспектива, часть 1"
    assert task.topic_id == video.topic_id


def test_video_already_standing_in_a_day_cannot_be_taken_by_another(
    client, db, user_factory, session_factory, monkeypatch
):
    """Один ролик — один день.

    Привязка живёт в единственной колонке `LearningVideo.topic_id`: второй день
    отобрал бы ролик у первого, и там осталась бы задача трекера без видео.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)
    everyone = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}

    first = client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={"catalog_video_id": video.id, "audience": everyone},
    )
    assert first.status_code == 200

    second = client.post(
        f"{PROGRAM}/2026-08-25/video",
        json={"catalog_video_id": video.id, "audience": everyone},
    )

    assert second.status_code == 422
    assert "24.08.2026" in second.json()["detail"]
    assert db.query(TrackerTask).count() == 1


def test_deleted_day_item_frees_its_video_for_another_day(
    client, db, user_factory, session_factory, monkeypatch
):
    """Удалили элемент дня — ролик снова свободен.

    Задача помечается `deleted_at`, а `topic_id` у ролика остаётся. Если считать
    занятость по одной этой колонке, ролик выпал бы из выбора навсегда: снять
    тему на вкладке загрузки больше нечем.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)
    everyone = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={"catalog_video_id": video.id, "audience": everyone},
    )
    task = db.query(TrackerTask).one()
    removed = client.post(f"{PROGRAM}/items/{task.id}/delete", json={})
    assert removed.status_code == 200

    again = client.post(
        f"{PROGRAM}/2026-08-25/video",
        json={"catalog_video_id": video.id, "audience": everyone},
    )

    assert again.status_code == 200
    db.expire_all()
    live = (
        db.query(TrackerTask)
        .filter(TrackerTask.deleted_at.is_(None), TrackerTask.kind == "video")
        .one()
    )
    assert db.get(LearningVideo, video.id).topic_id == live.topic_id


def test_catalog_videos_are_offered_on_the_day_page(
    client, db, user_factory, session_factory, monkeypatch
):
    """Форма дня показывает загруженные ролики, а занятые помечает."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    free = _video(db)
    free.title = "Свободный ролик"
    taken = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id="guid-program-2",
        title="Занятый ролик",
        status="ready",
    )
    db.add(taken)
    db.commit()
    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": taken.id,
            "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
        },
    )

    page = client.get(f"{PROGRAM}/2026-08-25")

    assert page.status_code == 200
    assert "Свободный ролик" in page.text
    assert "Занятый ролик" in page.text
    assert "Уже стоит в программе на 24.08.2026" in page.text


def test_day_page_embeds_configured_quiz_questions_for_picker(
    client, db, user_factory, session_factory, monkeypatch
):
    """Владелец 29.08.2026: конструктор вопросов должен появляться прямо там,
    где выбирают ролик на день — форма читает вопросы из JS-данных страницы,
    без отдельного похода на /cabinet/admin/videos. Кнопки «Сохранить вопросы»
    больше нет (владелец 29.08.2026, второй заход) — сохраняется по мере
    печати, см. `data-qq-text`/input-обработчик в скрипте."""
    import json

    from app.services.video_quiz import sync_questions

    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)
    sync_questions(db, video_id=video.id, items=[(None, "Что было важным?")])
    db.commit()

    page = client.get(f"{PROGRAM}/2026-08-25")

    assert page.status_code == 200
    # `tojson` отдаёт non-ASCII текст как \uXXXX-эскейпы — сравниваем с тем
    # же представлением, а не с сырой кириллицей.
    assert json.dumps("Что было важным?")[1:-1] in page.text
    assert 'data-add-quiz-question' in page.text
    assert 'data-save-quiz-questions' not in page.text


def test_video_opens_with_its_week_and_only_for_its_audience(
    client, db, user_factory, session_factory, monkeypatch
):
    """Доступ считает accessible_topic_ids по служебной теме.

    До понедельника своей недели ролик закрыт всем, с понедельника открыт тем,
    кому адресован: владелец решил 20.08, что ученик видит неделю целиком.
    """
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    inside = user_factory(vk_id=541_002, role_name="ученик")
    outside = user_factory(vk_id=541_003, role_name="ученик")
    db.add(UserTag(user_id=inside.id, tag_id=tag.id))
    db.commit()
    video = _video(db)
    video.status = "ready"
    video.is_published = True
    db.commit()

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": video.id,
            "title": "Перспектива",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )
    db.expire_all()
    video = db.get(LearningVideo, video.id)

    inside_viewer = {"user_id": inside.id, "role_rank": 1}
    outside_viewer = {"user_id": outside.id, "role_rank": 1}

    # Четверг перед неделей: закрыт даже своим.
    monkeypatch.setattr(
        "app.services.video_topics.now_msk",
        lambda: datetime(2026, 8, 21, 12, 0, tzinfo=MSK_TZ),
    )
    assert is_video_accessible(db, video, inside_viewer) is False

    # Понедельник этой недели: открыт своим и закрыт чужим.
    monkeypatch.setattr(
        "app.services.video_topics.now_msk",
        lambda: datetime(2026, 8, 24, 9, 0, tzinfo=MSK_TZ),
    )
    assert is_video_accessible(db, video, inside_viewer) is True
    assert is_video_accessible(db, video, outside_viewer) is False


def test_service_topic_is_hidden_from_the_week_list(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    video = _video(db)

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": video.id,
            "title": "Перспектива",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )

    assert list_topics(db) == []          # выпадающий список тем не зарос
    assert len(list_topics(db, kinds=None)) == 1


def test_unknown_video_gives_404(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": 9999,
            "title": "Нет такого",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 404


def test_cover_upload_goes_to_storage(client, user_factory, session_factory, monkeypatch):
    _staff_client(client, user_factory, session_factory)
    saved = {}

    def _fake_upload(path, data, content_type="image/jpeg"):
        saved["path"] = path
        return "https://s3.example/" + path

    monkeypatch.setattr(
        "app.api.cabinet_program.s3_service.upload_to_s3", _fake_upload
    )
    from io import BytesIO
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (12, 12), "white").save(buffer, format="PNG")

    response = client.post(
        f"{PROGRAM}/upload-cover",
        files={"file": ("cover.png", buffer.getvalue(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert saved["path"].startswith("Обложки видео/")


def test_non_image_cover_is_rejected(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{PROGRAM}/upload-cover",
        files={"file": ("plan.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 422


# ── Самостоятельная работа ────────────────────────────────────────────────

def test_homework_item_creates_its_own_entity(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/homework",
        json={
            "title": "Сдать натюрморт",
            "description": "Два листа",
            "subject": "Рисунок",
            "submission_required": True,
            "max_files": 2,
            "images": [{"url": "https://s3.example/ref.jpg", "path": "Домашние задания/ref.jpg"}],
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 200
    homework = db.query(HomeworkAssignment).one()
    assert homework.max_files == 2 and homework.created_by_id == admin.id
    assert db.query(HomeworkImage).count() == 1
    task = db.query(TrackerTask).one()
    assert task.kind == "homework" and task.source_id == homework.id
    assert task.topic_id is not None


def test_homework_shows_up_in_the_day(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    client.post(
        f"{PROGRAM}/{MONDAY}/homework",
        json={
            "title": "Сдать натюрморт",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )

    page = client.get(f"{PROGRAM}/{MONDAY}").text
    assert "Сдать натюрморт" in page
    assert "со сдачей работы" in page


def test_items_without_audience_are_rejected(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)

    empty = {"assign_to_all": False, "tag_ids": [], "assignee_usernames": ""}
    assert client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={"catalog_video_id": video.id, "title": "Ролик", "audience": empty},
    ).status_code == 422
    assert client.post(
        f"{PROGRAM}/{MONDAY}/homework", json={"title": "Задание", "audience": empty}
    ).status_code == 422


# ── is_required (гейт «блок → неделя → месяц», решение 23.08) ──────────────

def test_video_task_is_required_by_default(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    video = _video(db)

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={
            "catalog_video_id": video.id,
            "title": "Ролик",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )
    task = db.query(TrackerTask).one()
    assert task.is_required is True


def test_homework_task_can_be_marked_optional(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    client.post(
        f"{PROGRAM}/{MONDAY}/homework",
        json={
            "title": "Необязательная анкета-разминка",
            "is_required": False,
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )
    task = db.query(TrackerTask).one()
    assert task.is_required is False


def test_past_day_refuses_new_items(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/2026-08-17/homework",
        json={
            "title": "Задним числом",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 422
