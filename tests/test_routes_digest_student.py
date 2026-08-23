"""Дайджест месяца на экране ученика (/cabinet/tracker) — первый блок сверху."""

from app.models.tag import Tag, UserTag
from app.services.tracker import (
    create_digest,
    create_event,
    publish_digest,
    set_digest_tags,
)
from app.services.tz import today_msk

PAGE = "/cabinet/tracker"


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def test_student_sees_published_digest_addressed_to_them(client, db, user_factory, session_factory):
    student = user_factory(vk_id=430_001, name="Ученик", role_name="ученик")
    tag = _tag(db, "Поток 1")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Дайджест месяца", year=today.year, month=today.month,
        assign_to_all=False, user_id=student.id,
    )
    set_digest_tags(db, digest, [tag.id])
    create_event(
        db, digest.id, kind="mock_exam", title="Окно пробника", note=None,
        starts_on=today, ends_on=today, meeting_url=None,
    )
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Дайджест месяца" in response.text
    assert "Окно пробника" in response.text


def test_student_does_not_see_unpublished_digest(client, db, user_factory, session_factory):
    student = user_factory(vk_id=430_002, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Черновик месяца", year=today.year, month=today.month,
        assign_to_all=True, user_id=student.id,
    )
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Черновик месяца" not in response.text


def test_student_does_not_see_digest_for_other_tag(client, db, user_factory, session_factory):
    student = user_factory(vk_id=430_003, name="Ученик", role_name="ученик")
    other_tag = _tag(db, "Поток 2")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Дайджест другого потока", year=today.year, month=today.month,
        assign_to_all=False, user_id=student.id,
    )
    set_digest_tags(db, digest, [other_tag.id])
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Дайджест другого потока" not in response.text
