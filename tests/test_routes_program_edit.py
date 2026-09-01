"""Правка элементов дня программы вместо удаления-и-пересоздания.

Владелец 29.08.2026: сегодня и в будущем (см.
test_simple_item_edit_allows_on_the_day_itself) видео/самостоятельную/
материалы/тест по теории/занятие/чек-лист можно менять на месте — «Изменить»
рядом с «Удалить». Пробник (билеты, test_routes_program_mock_edit.py) и
Анкета (вопросы, ниже — только пока шаблон стоит ровно в одном дне) теперь
тоже редактируются, каждый своей отдельной стройкой 30.08.2026.
"""

import json
from datetime import date, datetime

from app.models.homework import HomeworkAssignment, HomeworkImage
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import TaskBlock
from app.models.tracker import ITEM_CHECKLIST, ITEM_MATERIAL, TrackerTask
from app.services.tracker import create_task
from app.services.tz import MSK_TZ, msk_midnight
from app.services.video_topics import get_topic_tariffs

PROGRAM = "/cabinet/staff/program"
TODAY = date(2026, 8, 21)
MONDAY = "2026-08-24"
TUESDAY = "2026-08-25"


def _staff_client(client, user_factory, session_factory, *, vk_id=540_200):
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


def _video(db, *, bunny_id: str = "guid-edit-1", status: str = "processing") -> LearningVideo:
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=bunny_id,
        title="Черновое название",
        status=status,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}


# ── Простые элементы ────────────────────────────────────────────────────────


def test_checklist_edit_round_trips_card_and_blocks(
    client, db, user_factory, session_factory, monkeypatch
):
    """Чек-лист правится на месте вместе с универсальным содержимым."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    created = client.post(
        f"{PROGRAM}/{MONDAY}/checklist",
        json={
            "title": "Перед занятием",
            "description": "Черновой список",
            "subject": "Рисунок",
            "audience": EVERYONE,
            "blocks": [
                {"block_type": "text", "body": "Проверьте материалы"},
                {"block_type": "text", "body": "Подготовьте рабочее место"},
            ],
        },
    )
    assert created.status_code == 200, created.text
    task = db.query(TrackerTask).filter(TrackerTask.kind == ITEM_CHECKLIST).one()
    original_blocks = (
        db.query(TaskBlock)
        .filter(TaskBlock.task_id == task.id)
        .order_by(TaskBlock.sort_order)
        .all()
    )

    page = client.get(f"{PROGRAM}/{MONDAY}").text
    article = page[page.index(f'data-item-id="{task.id}"'):]
    assert 'data-item-edit data-item-kind="checklist"' in article[:article.index("</article>")]
    edit_data = json.loads(page.split("programEditData = ")[1].split(";\n")[0])
    assert [block["id"] for block in edit_data[str(task.id)]["blocks"]] == [
        block.id for block in original_blocks
    ]

    updated = client.post(
        f"{PROGRAM}/items/{task.id}/checklist",
        json={
            "title": "Перед проверкой",
            "description": "Обновлённый список",
            "subject": "Композиция",
            "is_required": False,
            "audience": EVERYONE,
            "blocks": [
                {
                    "id": original_blocks[0].id,
                    "block_type": "text",
                    "body": "Проверьте материалы и референсы",
                },
                {"block_type": "text", "body": "Проверьте дедлайн"},
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    db.expire_all()

    task = db.get(TrackerTask, task.id)
    assert (task.title, task.description, task.subject, task.is_required) == (
        "Перед проверкой", "Обновлённый список", "Композиция", False,
    )
    saved_blocks = (
        db.query(TaskBlock)
        .filter(TaskBlock.task_id == task.id)
        .order_by(TaskBlock.sort_order)
        .all()
    )
    assert saved_blocks[0].id == original_blocks[0].id
    assert [block.body for block in saved_blocks] == [
        "Проверьте материалы и референсы", "Проверьте дедлайн",
    ]
    assert original_blocks[1].id not in [block.id for block in saved_blocks]


def test_simple_item_edit_updates_task_and_topic_title(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/material",
        json={"title": "Черновик", "subject": "Рисунок", "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    response = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json={
            "title": "Референс по композиции",
            "description": "Смотреть внимательно",
            "subject": "Композиция",
            "is_required": False,
            "audience": EVERYONE,
        },
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    task = db.get(TrackerTask, task.id)
    assert task.title == "Референс по композиции"
    assert task.description == "Смотреть внимательно"
    assert task.subject == "Композиция"
    assert task.is_required is False
    topic = db.get(LearningTopic, task.topic_id)
    assert topic.title == "Материал · Референс по композиции"


def test_simple_item_edit_round_trips_tariff_restriction(
    client, db, user_factory, session_factory, monkeypatch
):
    """Тариф правится и после создания — в отличие от тегов/поимённых (созвон 26.08.2026)."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/material",
        json={
            "title": "Материал",
            "audience": {
                "assign_to_all": True, "tag_ids": [], "assignee_usernames": "",
                "tariff_restricted": True, "tariffs": ["МАКСИМУМ"],
            },
        },
    )
    task = db.query(TrackerTask).one()
    db.expire_all()
    topic = db.get(LearningTopic, task.topic_id)
    assert topic.tariff_restricted is True
    assert get_topic_tariffs(db, topic.id) == ["МАКСИМУМ"]

    response = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json={
            "title": "Материал",
            "audience": {
                "assign_to_all": True, "tag_ids": [], "assignee_usernames": "",
                "tariff_restricted": True, "tariffs": ["УВЕРЕННЫЙ", "Я С ВАМИ"],
            },
        },
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    topic = db.get(LearningTopic, task.topic_id)
    assert set(get_topic_tariffs(db, topic.id)) == {"УВЕРЕННЫЙ", "Я С ВАМИ"}

    response = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json={"title": "Материал", "audience": EVERYONE},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    topic = db.get(LearningTopic, task.topic_id)
    assert topic.tariff_restricted is False
    assert get_topic_tariffs(db, topic.id) == []


def test_simple_item_edit_rejects_tariff_when_topic_shared_by_a_copied_week(
    client, db, user_factory, session_factory, monkeypatch
):
    """copy_week (app/services/tracker.py) кладёт все элементы копии на одну
    общую тему недели — тариф там нельзя настроить точечно на элементе, иначе
    он тихо скрыл бы всю неделю (архитектурный разбор 30.08.2026)."""
    _freeze(monkeypatch)
    owner = _staff_client(client, user_factory, session_factory)

    week_topic = LearningTopic(
        title="Неделя (копия)",
        opens_at=msk_midnight(date(2026, 8, 24)),
        assign_to_all=True,
        is_published=False,
        kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(week_topic)
    db.flush()
    task = create_task(
        db, title="Материал из копии недели", user_id=owner.id,
        kind=ITEM_MATERIAL, due_at=datetime(2026, 8, 25, 20, 59, tzinfo=MSK_TZ),
        topic_id=week_topic.id, assign_to_all=True,
    )
    task.is_published = True
    db.commit()

    response = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json={
            "title": "Материал из копии недели",
            "audience": {
                "assign_to_all": True, "tag_ids": [], "assignee_usernames": "",
                "tariff_restricted": True, "tariffs": ["МАКСИМУМ"],
            },
        },
    )

    assert response.status_code == 422
    db.expire_all()
    assert db.get(LearningTopic, week_topic.id).tariff_restricted is False


def test_simple_item_edit_wrong_kind_404(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/material",
        json={"title": "Материал", "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    response = client.post(
        f"{PROGRAM}/items/{task.id}/quiz",
        json={"title": "Другой вид", "audience": EVERYONE},
    )

    assert response.status_code == 404


def test_simple_item_edit_refuses_once_day_has_passed(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/checklist",
        json={"title": "Чек-лист", "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    _freeze(monkeypatch, date(2026, 8, 26))
    response = client.post(
        f"{PROGRAM}/items/{task.id}/checklist",
        json={"title": "Поздно", "audience": EVERYONE},
    )

    assert response.status_code == 422


def test_simple_item_edit_allows_on_the_day_itself(
    client, db, user_factory, session_factory, monkeypatch
):
    """Добавленное задание можно исправить в день публикации."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/checklist",
        json={"title": "Чек-лист", "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    _freeze(monkeypatch, date(2026, 8, 24))
    response = client.post(
        f"{PROGRAM}/items/{task.id}/checklist",
        json={"title": "Исправлено сегодня", "audience": EVERYONE},
    )

    assert response.status_code == 200
    db.refresh(task)
    assert task.title == "Исправлено сегодня"


def test_day_page_shows_edit_button_on_the_day_itself(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/material",
        json={"title": "Материал", "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    _freeze(monkeypatch, date(2026, 8, 24))
    page = client.get(f"{PROGRAM}/{MONDAY}").text

    article = page[page.index(f'data-item-id="{task.id}"'):]
    assert 'data-item-edit' in article[:article.index('</article>')]
    assert 'data-item-delete' in article[:article.index('</article>')]


# ── Самостоятельная работа ───────────────────────────────────────────────────

def test_homework_edit_updates_entity_and_replaces_images(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/homework",
        json={
            "title": "Сдать натюрморт",
            "max_files": 1,
            "images": [{"url": "https://s3.example/old.jpg", "path": "old.jpg"}],
            "audience": EVERYONE,
        },
    )
    task = db.query(TrackerTask).one()
    homework = db.query(HomeworkAssignment).one()

    response = client.post(
        f"{PROGRAM}/items/{task.id}/homework",
        json={
            "title": "Сдать натюрморт, версия 2",
            "submission_required": False,
            "max_files": 3,
            "images": [{"url": "https://s3.example/new.jpg", "path": "new.jpg"}],
            "audience": EVERYONE,
        },
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    homework = db.get(HomeworkAssignment, homework.id)
    assert homework.title == "Сдать натюрморт, версия 2"
    assert homework.submission_required is False
    assert homework.max_files == 3
    images = db.query(HomeworkImage).filter(HomeworkImage.homework_id == homework.id).all()
    assert [i.image_s3_url for i in images] == ["https://s3.example/new.jpg"]
    task = db.get(TrackerTask, task.id)
    assert task.title == "Сдать натюрморт, версия 2"


# ── Видео ────────────────────────────────────────────────────────────────────

def test_video_edit_keeps_same_video_without_taken_conflict(
    client, db, user_factory, session_factory, monkeypatch
):
    """Ролик, уже стоящий в этом же дне, не должен считаться занятым им самим."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={"catalog_video_id": video.id, "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    response = client.post(
        f"{PROGRAM}/items/{task.id}/video",
        json={
            "catalog_video_id": video.id,
            "title": "Обновлённое название",
            "subject": "Рисунок",
            "audience": EVERYONE,
        },
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    video = db.get(LearningVideo, video.id)
    assert video.title == "Обновлённое название"
    task = db.get(TrackerTask, task.id)
    assert task.subject == "Рисунок"
    assert task.topic_id == video.topic_id


def test_video_edit_updates_description_and_can_clear_it(
    client, db, user_factory, session_factory, monkeypatch
):
    """Название и описание ролика — то же, что указано при загрузке, и их
    можно менять прямо из дня программы (владелец 29.08.2026, второй заход)."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    video = _video(db)
    video.description = "Старое описание"
    db.commit()

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={"catalog_video_id": video.id, "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    # Ключ вовсе не прислан — описание не трогаем.
    response = client.post(
        f"{PROGRAM}/items/{task.id}/video",
        json={"catalog_video_id": video.id, "audience": EVERYONE},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(LearningVideo, video.id).description == "Старое описание"

    # Прислали пустую строку — это осознанная очистка (форма всегда шлёт
    # текущее значение поля, включая пустое).
    response = client.post(
        f"{PROGRAM}/items/{task.id}/video",
        json={"catalog_video_id": video.id, "description": "", "audience": EVERYONE},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(LearningVideo, video.id).description is None


def test_day_page_video_form_has_title_and_description_fields(
    client, db, user_factory, session_factory, monkeypatch
):
    """Поля видны на форме дня — редактировать ролик можно, не уходя на
    вкладку «Загрузка видео» (владелец 29.08.2026, второй заход)."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _video(db, status="ready")

    page = client.get(f"{PROGRAM}/{MONDAY}").text

    assert 'data-v-title' in page
    assert 'data-v-description' in page
    assert '<select data-v-pick aria-label="Выберите видео">' in page
    assert 'type="radio" name="catalog-video"' not in page
    assert page.index('data-v-title') < page.index('data-v-description') < page.index('data-v-pick')
    assert "Дополнительные задачи учебного дня" in page


def test_video_edit_swaps_to_another_free_video(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    old_video = _video(db, bunny_id="guid-edit-old")
    new_video = _video(db, bunny_id="guid-edit-new", status="ready")

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={"catalog_video_id": old_video.id, "audience": EVERYONE},
    )
    task = db.query(TrackerTask).one()

    response = client.post(
        f"{PROGRAM}/items/{task.id}/video",
        json={"catalog_video_id": new_video.id, "audience": EVERYONE},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    old_video = db.get(LearningVideo, old_video.id)
    new_video = db.get(LearningVideo, new_video.id)
    task = db.get(TrackerTask, task.id)
    assert old_video.topic_id is None
    assert new_video.topic_id == task.topic_id
    assert new_video.is_published is True


def test_video_edit_rejects_video_taken_by_another_day(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    monday_video = _video(db, bunny_id="guid-edit-mon")
    tuesday_video = _video(db, bunny_id="guid-edit-tue")

    client.post(
        f"{PROGRAM}/{MONDAY}/video",
        json={"catalog_video_id": monday_video.id, "audience": EVERYONE},
    )
    client.post(
        f"{PROGRAM}/{TUESDAY}/video",
        json={"catalog_video_id": tuesday_video.id, "audience": EVERYONE},
    )
    monday_task = (
        db.query(TrackerTask)
        .filter(TrackerTask.topic_id == monday_video.topic_id)
        .one()
    )

    response = client.post(
        f"{PROGRAM}/items/{monday_task.id}/video",
        json={"catalog_video_id": tuesday_video.id, "audience": EVERYONE},
    )

    assert response.status_code == 422
    assert "25.08.2026" in response.json()["detail"]
    db.expire_all()
    assert db.get(LearningVideo, monday_video.id).topic_id == monday_task.topic_id


# ── Карточка дня: кнопка «Изменить» только у покрытых видов ─────────────────

def test_day_page_offers_edit_only_for_editable_kinds(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    client.post(
        f"{PROGRAM}/{MONDAY}/material",
        json={"title": "Материал для правки", "audience": EVERYONE},
    )
    material_task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()

    page = client.get(f"{PROGRAM}/{MONDAY}").text

    material_article = page[page.index(f'data-item-id="{material_task.id}"'):]
    assert 'data-item-edit' in material_article[:material_article.index('</article>')]

    edit_data_json = page.split('programEditData = ')[1].split(';\n')[0]
    edit_data = json.loads(edit_data_json)
    assert str(material_task.id) in edit_data


def test_survey_is_editable_like_any_other_item(
    client, db, user_factory, session_factory, monkeypatch
):
    """Прежнее правило «анкету-шаблон нельзя править, если она стоит в
    нескольких днях» снято 31.08.2026: вопросы стали блоками задания, и правка
    одного дня физически не может задеть другой."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    day_iso = MONDAY

    created = client.post(
        f"{PROGRAM}/{day_iso}/survey",
        json={"title": "Анкета недели", "audience": EVERYONE},
    )
    assert created.status_code == 200, created.text

    task = db.query(TrackerTask).filter(TrackerTask.kind == "survey").one()
    page = client.get(f"{PROGRAM}/{day_iso}")
    assert page.status_code == 200
    payload = json.loads(page.text.split("programEditData = ")[1].split(";" + chr(10))[0])
    assert str(task.id) in payload
