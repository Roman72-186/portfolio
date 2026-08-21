"""Конструктор недели: элементы программы по дням и домашка как своя сущность."""

from datetime import datetime, timedelta, timezone

from app.models.homework import HomeworkAssignment, HomeworkImage
from app.models.learning_topic import LearningTopic, LearningTopicTag
from app.models.tag import Tag, UserTag
from app.models.tracker import TrackerTask


TRACKER = "/cabinet/staff/tracker"


def _staff_client(client, user_factory, session_factory, *, role_name="админ", vk_id=420_004):
    user = user_factory(
        vk_id=vk_id,
        name="Главный преподаватель",
        is_admin=role_name in ("админ", "суперадмин"),
        is_group_member=False,
        role_name=role_name,
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _week(db, *, title="Неделя 1", opens_days_ahead=0, published=True) -> LearningTopic:
    week = LearningTopic(
        title=title,
        opens_at=datetime.now(timezone.utc) + timedelta(days=opens_days_ahead),
        assign_to_all=True,
        is_published=published,
    )
    db.add(week)
    db.commit()
    db.refresh(week)
    return week


def _items_url(week: LearningTopic) -> str:
    return f"{TRACKER}/weeks/{week.id}/items"


# ── Экран ─────────────────────────────────────────────────────────────────

def test_week_constructor_sends_to_the_program_calendar(
    client, db, user_factory, session_factory, auth_client
):
    """Экран снят 21.08: программа собирается в «Учебных программах».

    Роуты сохранения элементов пока живут — на них стоят тесты ниже, — но входа
    в старый конструктор нет, чтобы человек не собирал программу в двух местах.
    """
    week = _week(db)
    student_client, _ = auth_client
    assert student_client.get(f"{TRACKER}/weeks/{week.id}").status_code == 403

    student_client.cookies.clear()
    _staff_client(client, user_factory, session_factory)
    response = client.get(f"{TRACKER}/weeks/{week.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/cabinet/staff/program"


def test_task_screen_no_longer_lists_weeks(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    _week(db, title="Неделя перспективы")

    page = client.get(TRACKER)

    assert "Недели программы" not in page.text
    assert "Собрать программу" not in page.text
    assert "Новая задача" in page.text      # разовые задачи остаются


# ── Сборка недели ─────────────────────────────────────────────────────────

def test_admin_composes_week_day_by_day(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    week = _week(db)

    client.post(_items_url(week), json={
        "title": "Посмотреть видео недели", "kind": "video", "due_at": "2026-08-21T12:00",
    })
    client.post(_items_url(week), json={
        "title": "Пробник со сдачей работы", "kind": "mock_exam", "due_at": "2026-08-22T18:00",
        "subject": "Рисунок",
    })
    client.post(_items_url(week), json={
        "title": "Анкета по занятию", "kind": "survey", "due_at": "2026-08-23T20:00",
    })

    items = db.query(TrackerTask).order_by(TrackerTask.due_at).all()
    assert [i.kind for i in items] == ["video", "mock_exam", "survey"]
    assert all(i.topic_id == week.id for i in items)
    # Элемент недели публикуется вместе с ней: отдельной кнопки у строки нет.
    assert all(i.is_published for i in items)
    # Видео привязано к своей неделе, остальное гасит ученик галочкой.
    assert items[0].source_kind == "learning_topic" and items[0].source_id == week.id
    assert items[1].source_kind is None

    assert [i.due_at.strftime("%d.%m") for i in items if i.due_at] == ["21.08", "22.08", "23.08"]


def test_week_items_do_not_show_up_among_standalone_tasks(
    client, db, user_factory, session_factory
):
    """Иначе один и тот же элемент висел бы на двух экранах сразу."""
    _staff_client(client, user_factory, session_factory)
    week = _week(db)
    client.post(_items_url(week), json={"title": "Элемент недели", "kind": "video"})
    client.post(f"{TRACKER}/tasks", json={"title": "Разовая задача", "assign_to_all": True})

    page = client.get(TRACKER)

    assert "Разовая задача" in page.text
    assert page.text.count("Элемент недели") == 0


def test_unknown_item_kind_is_rejected(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    week = _week(db)

    response = client.post(_items_url(week), json={"title": "Что-то", "kind": "квест"})

    assert response.status_code == 422


def test_item_of_another_week_is_not_editable_through_wrong_week(
    client, db, user_factory, session_factory
):
    _staff_client(client, user_factory, session_factory)
    first = _week(db, title="Неделя 1")
    second = _week(db, title="Неделя 2")
    task_id = client.post(_items_url(first), json={"title": "Видео", "kind": "video"}).json()["task_id"]

    response = client.post(
        f"{TRACKER}/weeks/{second.id}/items/{task_id}",
        json={"title": "Подмена", "kind": "video"},
    )

    assert response.status_code == 404


def test_week_item_deletes_without_unpublishing_first(
    client, db, user_factory, session_factory
):
    """У элемента нет своей публикации — требовать «сначала скройте» было бы
    требованием снять всю неделю."""
    _staff_client(client, user_factory, session_factory)
    week = _week(db)
    task_id = client.post(_items_url(week), json={"title": "Лишний", "kind": "other"}).json()["task_id"]

    assert client.post(f"{TRACKER}/tasks/{task_id}/delete").status_code == 200
    db.expire_all()
    assert db.get(TrackerTask, task_id).deleted_at is not None


# ── Домашка ───────────────────────────────────────────────────────────────

def test_homework_is_created_as_its_own_entity(client, db, user_factory, session_factory):
    """Решение владельца по Р2: домашка — не ещё один kind у ExamAssignment."""
    admin = _staff_client(client, user_factory, session_factory)
    week = _week(db)

    task_id = client.post(_items_url(week), json={
        "title": "Сдать натюрморт",
        "description": "Два листа, карандаш",
        "kind": "homework",
        "subject": "Рисунок",
        "due_at": "2026-08-22T18:00",
        "homework": {
            "submission_required": True,
            "max_files": 2,
            "images": [{"url": "https://s3.example/ref-1.jpg", "path": "Домашние задания/ref-1.jpg"}],
        },
    }).json()["task_id"]

    homework = db.query(HomeworkAssignment).one()
    assert homework.title == "Сдать натюрморт"
    assert homework.subject == "Рисунок"
    assert homework.max_files == 2
    assert homework.created_by_id == admin.id
    assert db.query(HomeworkImage).count() == 1

    task = db.get(TrackerTask, task_id)
    assert task.source_kind == "homework" and task.source_id == homework.id


def test_editing_homework_item_updates_the_same_entity(
    client, db, user_factory, session_factory
):
    _staff_client(client, user_factory, session_factory)
    week = _week(db)
    task_id = client.post(_items_url(week), json={
        "title": "Натюрморт", "kind": "homework",
        "homework": {"submission_required": True, "max_files": 1, "images": []},
    }).json()["task_id"]

    client.post(f"{TRACKER}/weeks/{week.id}/items/{task_id}", json={
        "title": "Натюрморт с драпировкой", "kind": "homework",
        "homework": {
            "submission_required": False, "max_files": 3,
            "images": [{"url": "https://s3.example/ref-2.jpg", "path": None}],
        },
    })

    db.expire_all()
    assert db.query(HomeworkAssignment).count() == 1     # не плодим дубликаты
    homework = db.query(HomeworkAssignment).one()
    assert homework.title == "Натюрморт с драпировкой"
    assert homework.submission_required is False
    assert homework.max_files == 3
    assert [i.image_s3_url for i in db.query(HomeworkImage).all()] == ["https://s3.example/ref-2.jpg"]


def test_homework_images_survive_a_plain_edit(client, db, user_factory, session_factory):
    """Форма присылает картинки обратно — иначе правка задания стирала бы референсы."""
    _staff_client(client, user_factory, session_factory)
    week = _week(db)
    payload = {
        "title": "Задание с референсом", "kind": "homework",
        "homework": {
            "submission_required": True, "max_files": 1,
            "images": [{"url": "https://s3.example/ref.jpg", "path": "Домашние задания/ref.jpg"}],
        },
    }
    task_id = client.post(_items_url(week), json=payload).json()["task_id"]

    client.post(f"{TRACKER}/weeks/{week.id}/items/{task_id}", json=payload)
    db.expire_all()
    assert db.query(HomeworkImage).count() == 1


# ── Копирование недели ────────────────────────────────────────────────────

def test_copy_week_shifts_dates_and_stays_a_draft(
    client, db, user_factory, session_factory
):
    _staff_client(client, user_factory, session_factory)
    tag = Tag(name="Поток 1")
    db.add(tag)
    db.commit()
    week = _week(db, title="Неделя 1", published=True)
    week.assign_to_all = False
    db.add(LearningTopicTag(topic_id=week.id, tag_id=tag.id))
    db.commit()
    student = user_factory(vk_id=422_001, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()

    client.post(_items_url(week), json={
        "title": "Видео недели", "kind": "video", "due_at": "2026-08-21T12:00",
    })
    client.post(_items_url(week), json={
        "title": "Домашка", "kind": "homework", "due_at": "2026-08-22T18:00",
        "homework": {"submission_required": True, "max_files": 1,
                     "images": [{"url": "https://s3.example/ref.jpg", "path": None}]},
    })

    response = client.post(f"{TRACKER}/weeks/{week.id}/copy")
    assert response.status_code == 200
    copy_id = response.json()["topic_id"]

    copy = db.get(LearningTopic, copy_id)
    assert copy.is_published is False            # копия не уезжает ученикам сама
    assert copy.opens_at == week.opens_at + timedelta(days=7)
    assert [row.tag_id for row in db.query(LearningTopicTag).filter_by(topic_id=copy.id)] == [tag.id]

    original = db.query(TrackerTask).filter_by(topic_id=week.id).order_by(TrackerTask.due_at).all()
    copied = db.query(TrackerTask).filter_by(topic_id=copy.id).order_by(TrackerTask.due_at).all()
    assert [t.title for t in copied] == [t.title for t in original]
    assert copied[0].due_at == original[0].due_at + timedelta(days=7)
    # Видео копии смотрит на копию недели, а не на прошлую.
    assert copied[0].source_id == copy.id

    # Домашка дублируется: правка в новой неделе не должна менять сданное задание.
    assert db.query(HomeworkAssignment).count() == 2
    assert copied[1].source_id != original[1].source_id
    assert db.query(HomeworkImage).count() == 2


def test_reference_image_upload_goes_to_storage(
    client, user_factory, session_factory, monkeypatch
):
    """Картинка задания уезжает в S3, а ключ хранилища не попадает в ответ."""
    _staff_client(client, user_factory, session_factory)
    saved: dict = {}

    def _fake_upload(path, data, content_type="image/jpeg"):
        saved["path"] = path
        saved["content_type"] = content_type
        saved["bytes"] = len(data)
        return "https://s3.example/" + path

    monkeypatch.setattr(
        "app.api.cabinet_tracker_admin.s3_service.upload_to_s3", _fake_upload
    )

    from io import BytesIO
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (12, 12), "white").save(buffer, format="PNG")

    response = client.post(
        f"{TRACKER}/homework/upload-image",
        files={"file": ("reference.png", buffer.getvalue(), "image/png")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["url"].startswith("https://s3.example/")
    assert saved["path"].startswith("Домашние задания/")
    assert saved["content_type"] == "image/jpeg"


def test_non_image_upload_is_rejected(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{TRACKER}/homework/upload-image",
        files={"file": ("plan.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["ok"] is False


def test_student_cannot_upload_reference_images(auth_client):
    client, _ = auth_client
    response = client.post(
        f"{TRACKER}/homework/upload-image",
        files={"file": ("reference.png", b"whatever", "image/png")},
    )
    assert response.status_code == 403


def test_copied_week_audience_matches_the_original(
    client, db, user_factory, session_factory
):
    _staff_client(client, user_factory, session_factory)
    user_factory(vk_id=423_001, role_name="ученик")
    week = _week(db, title="Неделя всем")

    copy_id = client.post(f"{TRACKER}/weeks/{week.id}/copy").json()["topic_id"]

    from app.services.tracker import week_audience

    assert week_audience(db, db.get(LearningTopic, copy_id)) == 1
