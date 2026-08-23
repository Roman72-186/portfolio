"""Экран Главного преподавателя: «Ближайшая цель» (/cabinet/staff/goals)."""

from app.models.tag import Tag, UserTag
from app.models.tracker import TrackerGoal

PAGE = "/cabinet/staff/goals"


def _staff_client(client, user_factory, session_factory, *, role_name="админ", vk_id=440_004):
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


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _student_with_tag(db, user_factory, tag: Tag, *, vk_id: int):
    student = user_factory(vk_id=vk_id, name=f"Ученик {vk_id}", role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    return student


def test_moderator_cannot_open_goal_admin(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory, role_name="модератор", vk_id=440_003)
    assert client.get(PAGE).status_code == 403


def test_admin_opens_goal_admin(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Ближайшая цель" in response.text


def test_student_cannot_open_goal_admin(auth_client):
    client, _ = auth_client
    assert client.get(PAGE).status_code == 403


def test_admin_creates_goal_with_real_audience(client, db, user_factory, session_factory):
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "Поток 1")
    _student_with_tag(db, user_factory, tag, vk_id=441_001)

    response = client.post(
        PAGE,
        json={
            "title": "Пробник по рисунку",
            "description": "Финал полугодия",
            "target_score": 75,
            "starts_on": "2026-09-25",
            "ends_on": "2026-09-30",
            "assign_to_all": False,
            "tag_ids": [tag.id],
            "assignee_usernames": "",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["audience_size"] == 1
    goal = db.query(TrackerGoal).one()
    assert goal.created_by_id == admin.id
    assert goal.target_score == 75
    assert goal.is_published is False


def test_goal_ends_before_starts_is_rejected(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    response = client.post(
        PAGE,
        json={
            "title": "Цель", "assign_to_all": True, "tag_ids": [], "assignee_usernames": "",
            "starts_on": "2026-09-10", "ends_on": "2026-09-05",
        },
    )
    assert response.status_code == 422


def test_publish_unpublish_and_delete_lifecycle(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    create_resp = client.post(
        PAGE, json={"title": "Цель", "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    goal_id = create_resp.json()["goal_id"]

    publish_resp = client.post(f"{PAGE}/{goal_id}/publish")
    assert publish_resp.status_code == 200

    delete_blocked = client.post(f"{PAGE}/{goal_id}/delete")
    assert delete_blocked.status_code == 409

    unpublish_resp = client.post(f"{PAGE}/{goal_id}/unpublish")
    assert unpublish_resp.status_code == 200

    delete_resp = client.post(f"{PAGE}/{goal_id}/delete")
    assert delete_resp.status_code == 200
    goal = db.get(TrackerGoal, goal_id)
    assert goal.deleted_at is not None
