"""«Ближайшая цель» на экране ученика (/cabinet/tracker)."""

from datetime import timedelta

from app.models.tag import Tag, UserTag
from app.services.tracker import create_goal, publish_goal, set_goal_tags
from app.services.tz import today_msk

PAGE = "/cabinet/tracker"


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def test_student_sees_nearest_published_goal(client, db, user_factory, session_factory):
    student = user_factory(vk_id=450_001, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    goal = create_goal(
        db, title="Пробник по рисунку", user_id=student.id, target_score=75,
        starts_on=today + timedelta(days=5), ends_on=today + timedelta(days=10),
        assign_to_all=True,
    )
    publish_goal(goal, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Пробник по рисунку" in response.text
    assert "75" in response.text


def test_student_does_not_see_unpublished_goal(client, db, user_factory, session_factory):
    student = user_factory(vk_id=450_002, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    create_goal(db, title="Черновая цель", user_id=student.id, assign_to_all=True)
    db.commit()

    response = client.get(PAGE)
    assert "Черновая цель" not in response.text


def test_student_does_not_see_expired_goal(client, db, user_factory, session_factory):
    student = user_factory(vk_id=450_003, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    goal = create_goal(
        db, title="Прошедшая цель", user_id=student.id, assign_to_all=True,
        starts_on=today - timedelta(days=20), ends_on=today - timedelta(days=10),
    )
    publish_goal(goal, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert "Прошедшая цель" not in response.text


def test_nearest_goal_wins_over_farther_one(client, db, user_factory, session_factory):
    student = user_factory(vk_id=450_004, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    far = create_goal(
        db, title="Дальняя цель", user_id=student.id, assign_to_all=True,
        starts_on=today + timedelta(days=30), ends_on=today + timedelta(days=35),
    )
    publish_goal(far, user_id=student.id)
    near = create_goal(
        db, title="Близкая цель", user_id=student.id, assign_to_all=True,
        starts_on=today + timedelta(days=2), ends_on=today + timedelta(days=5),
    )
    publish_goal(near, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert "Близкая цель" in response.text
    assert "Дальняя цель" not in response.text
