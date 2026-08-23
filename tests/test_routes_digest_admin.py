"""Экран Главного преподавателя: дайджест-расписание месяца (/cabinet/staff/digest)."""

from app.models.tag import Tag, UserTag
from app.models.tracker import ScheduleDigest, ScheduleEvent

PAGE = "/cabinet/staff/digest"


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


# ── Доступ ────────────────────────────────────────────────────────────────

def test_moderator_cannot_open_digest_admin(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory, role_name="модератор", vk_id=420_003)
    assert client.get(PAGE).status_code == 403


def test_admin_opens_digest_admin(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Дайджест-расписание" in response.text


def test_student_cannot_open_digest_admin(auth_client):
    client, _ = auth_client
    assert client.get(PAGE).status_code == 403


# ── Создание и адресация ────────────────────────────────────────────────

def test_admin_creates_digest_with_real_audience(client, db, user_factory, session_factory):
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "Поток 1")
    _student_with_tag(db, user_factory, tag, vk_id=421_001)

    response = client.post(
        PAGE,
        json={
            "title": "Сентябрь — поток 1",
            "year": 2026,
            "month": 9,
            "assign_to_all": False,
            "tag_ids": [tag.id],
            "assignee_usernames": "",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["audience_size"] == 1
    digest = db.query(ScheduleDigest).one()
    assert digest.created_by_id == admin.id
    assert digest.is_published is False
    assert digest.year == 2026
    assert digest.month == 9


def test_update_digest_rewrites_audience(client, db, user_factory, session_factory):
    admin = _staff_client(client, user_factory, session_factory)
    tag_a = _tag(db, "Поток A")
    tag_b = _tag(db, "Поток B")
    _student_with_tag(db, user_factory, tag_a, vk_id=421_002)
    _student_with_tag(db, user_factory, tag_b, vk_id=421_003)

    create_resp = client.post(
        PAGE,
        json={
            "title": "Сентябрь", "year": 2026, "month": 9,
            "assign_to_all": False, "tag_ids": [tag_a.id], "assignee_usernames": "",
        },
    )
    digest_id = create_resp.json()["digest_id"]

    update_resp = client.post(
        f"{PAGE}/{digest_id}",
        json={
            "title": "Сентябрь (обновлено)", "year": 2026, "month": 9,
            "assign_to_all": False, "tag_ids": [tag_b.id], "assignee_usernames": "",
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["audience_size"] == 1
    digest = db.get(ScheduleDigest, digest_id)
    assert digest.title == "Сентябрь (обновлено)"


# ── Публикация и удаление ───────────────────────────────────────────────

def test_publish_unpublish_and_delete_lifecycle(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    create_resp = client.post(
        PAGE,
        json={"title": "Октябрь", "year": 2026, "month": 10, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    digest_id = create_resp.json()["digest_id"]

    # Опубликованный дайджест нельзя удалить сразу.
    publish_resp = client.post(f"{PAGE}/{digest_id}/publish")
    assert publish_resp.status_code == 200
    digest = db.get(ScheduleDigest, digest_id)
    assert digest.is_published is True

    delete_blocked = client.post(f"{PAGE}/{digest_id}/delete")
    assert delete_blocked.status_code == 409

    unpublish_resp = client.post(f"{PAGE}/{digest_id}/unpublish")
    assert unpublish_resp.status_code == 200
    db.refresh(digest)
    assert digest.is_published is False

    delete_resp = client.post(f"{PAGE}/{digest_id}/delete")
    assert delete_resp.status_code == 200
    db.refresh(digest)
    assert digest.deleted_at is not None


# ── События ──────────────────────────────────────────────────────────────

def test_events_crud_inside_digest(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    create_resp = client.post(
        PAGE,
        json={"title": "Ноябрь", "year": 2026, "month": 11, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    digest_id = create_resp.json()["digest_id"]

    events_page = client.get(f"{PAGE}/{digest_id}/events")
    assert events_page.status_code == 200

    create_event_resp = client.post(
        f"{PAGE}/{digest_id}/events",
        json={
            "kind": "mock_exam", "title": "Окно пробника",
            "note": None, "starts_on": "2026-11-25", "ends_on": "2026-11-30",
            "meeting_url": None, "sort_order": 0,
        },
    )
    assert create_event_resp.status_code == 200
    event_id = create_event_resp.json()["event_id"]
    event = db.get(ScheduleEvent, event_id)
    assert event.digest_id == digest_id
    assert event.starts_on.isoformat() == "2026-11-25"

    update_resp = client.post(
        f"{PAGE}/{digest_id}/events/{event_id}",
        json={
            "kind": "mock_exam", "title": "Окно пробника (сдвинуто)",
            "note": "Финал", "starts_on": "2026-11-26", "ends_on": "2026-11-30",
            "meeting_url": "https://example.com/call", "sort_order": 0,
        },
    )
    assert update_resp.status_code == 200
    db.refresh(event)
    assert event.title == "Окно пробника (сдвинуто)"
    assert event.meeting_url == "https://example.com/call"

    delete_resp = client.post(f"{PAGE}/{digest_id}/events/{event_id}/delete")
    assert delete_resp.status_code == 200
    assert db.get(ScheduleEvent, event_id) is None


def test_event_ends_before_starts_is_rejected(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    create_resp = client.post(
        PAGE,
        json={"title": "Декабрь", "year": 2026, "month": 12, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    digest_id = create_resp.json()["digest_id"]

    response = client.post(
        f"{PAGE}/{digest_id}/events",
        json={
            "kind": "deadline", "title": "Дедлайн",
            "note": None, "starts_on": "2026-12-10", "ends_on": "2026-12-05",
            "meeting_url": None, "sort_order": 0,
        },
    )
    assert response.status_code == 422
