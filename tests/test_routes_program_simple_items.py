"""Материалы, тест по теории, занятие, чек-лист — простые элементы дня без
своей сущности (в отличие от видео/домашки/пробника/анкеты).

Обнаружено 23.08: в АОП ученика 8 вкладок, а конструктор умел создавать
элементы только для 4 из них. Эти четыре роута закрывают разрыв.
"""

from datetime import date

from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, LearningTopic
from app.models.tag import Tag, UserTag
from app.models.tracker import TrackerTask

PROGRAM = "/cabinet/staff/program"
TODAY = date(2026, 8, 21)
MONDAY = "2026-08-24"


def _staff_client(client, user_factory, session_factory, *, vk_id=540_100):
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


SIMPLE_KINDS = ("material", "quiz", "lesson", "checklist")


def test_each_simple_kind_creates_task_and_topic(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    student = user_factory(vk_id=541_101, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()

    for kind in SIMPLE_KINDS:
        response = client.post(
            f"{PROGRAM}/{MONDAY}/{kind}",
            json={
                "title": f"Элемент {kind}",
                "description": "Подробности",
                "subject": "Рисунок",
                "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
            },
        )
        assert response.status_code == 200, (kind, response.text)
        assert response.json()["audience_size"] == 1

    tasks = db.query(TrackerTask).order_by(TrackerTask.id.asc()).all()
    assert [t.kind for t in tasks] == list(SIMPLE_KINDS)
    for task in tasks:
        assert task.created_by_id == admin.id
        assert task.is_published is True
        assert task.is_required is True
        assert task.topic_id is not None
        topic = db.get(LearningTopic, task.topic_id)
        assert topic.kind == TOPIC_KIND_PROGRAM_ITEM


def test_simple_item_shows_up_in_the_day(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    client.post(
        f"{PROGRAM}/{MONDAY}/material",
        json={
            "title": "Референс по композиции",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )

    page = client.get(f"{PROGRAM}/{MONDAY}").text
    assert "Референс по композиции" in page


def test_simple_item_can_be_marked_optional(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    client.post(
        f"{PROGRAM}/{MONDAY}/checklist",
        json={
            "title": "Необязательный чек-лист",
            "is_required": False,
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )
    task = db.query(TrackerTask).one()
    assert task.is_required is False


def test_simple_item_without_audience_is_rejected(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    empty = {"assign_to_all": False, "tag_ids": [], "assignee_usernames": ""}
    for kind in SIMPLE_KINDS:
        response = client.post(
            f"{PROGRAM}/{MONDAY}/{kind}",
            json={"title": "Без адресации", "audience": empty},
        )
        assert response.status_code == 422, kind


def test_simple_item_refuses_past_day(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/2026-08-17/material",
        json={
            "title": "Задним числом",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )
    assert response.status_code == 422


def test_student_marks_simple_item_manually(
    client, db, user_factory, session_factory, monkeypatch
):
    """У материала/теста/занятия/чек-листа нет source_kind — ученик закрывает
    их обычной галочкой «Отметить», как решает `task_action.html`."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    student = user_factory(vk_id=541_200, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()

    client.post(
        f"{PROGRAM}/{MONDAY}/quiz",
        json={
            "title": "Тест по перспективе",
            "audience": {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""},
        },
    )
    task = db.query(TrackerTask).one()
    assert task.source_kind is None
