"""Куратор сам меняет своё имя (владелец 30.09.2026: кураторы заведены как
«Куратор 1…7», переименоваться должны сами, и «чтобы в системе они были уже
переименованы везде»)."""
from app.models.audit_log import AuditLog
from app.models.tag import Tag
from app.models.user import User


def _curator(db, user_factory, vk_id=996_001):
    curator = user_factory(vk_id=vk_id, name="Куратор 1", role_name="куратор")
    curator.first_name = "Куратор"
    curator.last_name = "1"
    db.commit()
    return curator


def test_curator_opens_form_with_current_name(client, db, user_factory, session_factory):
    curator = _curator(db, user_factory)
    client.cookies.set("session_id", session_factory(curator).id)

    page = client.get("/cabinet/curator/profile")

    assert page.status_code == 200
    assert 'name="first_name"' in page.text
    assert 'value="Куратор"' in page.text
    assert 'value="1"' in page.text


def test_dashboard_links_to_rename(client, db, user_factory, session_factory):
    curator = _curator(db, user_factory)
    client.cookies.set("session_id", session_factory(curator).id)

    assert 'href="/cabinet/curator/profile"' in client.get("/cabinet/curator").text


def test_rename_updates_name_copies_and_tag(client, db, user_factory, session_factory):
    curator = _curator(db, user_factory)
    other = _curator(db, user_factory, vk_id=996_002)
    own = user_factory(vk_id=996_010, name="Свой")
    own.curator_id = curator.id
    own.curator_tag = "1 Куратор"
    manual = user_factory(vk_id=996_011, name="Свой с ручной подписью")
    manual.curator_id = curator.id
    manual.curator_tag = "Группа Б"
    foreign = user_factory(vk_id=996_012, name="Чужой")
    foreign.curator_id = other.id
    foreign.curator_tag = "1 Куратор"
    db.add(Tag(name="1 Куратор"))
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)

    resp = client.post(
        "/cabinet/curator/profile",
        data={"first_name": " Анна ", "last_name": "Смирнова"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/curator?renamed=1"
    db.expire_all()
    me = db.get(User, curator.id)
    assert (me.first_name, me.last_name, me.name) == ("Анна", "Смирнова", "Анна Смирнова")
    assert db.get(User, own.id).curator_tag == "Смирнова Анна"
    assert db.get(User, manual.id).curator_tag == "Группа Б"
    assert db.get(User, foreign.id).curator_tag == "1 Куратор"
    assert db.query(Tag).filter(Tag.name == "Смирнова Анна").count() == 1
    assert db.query(Tag).filter(Tag.name == "1 Куратор").count() == 0
    assert db.query(AuditLog).filter(AuditLog.action == "user_rename").count() == 1
    assert "Имя сохранено" in client.get("/cabinet/curator?renamed=1").text


def test_rename_leaves_old_tag_when_new_name_is_taken(client, db, user_factory, session_factory):
    curator = _curator(db, user_factory)
    db.add_all([Tag(name="1 Куратор"), Tag(name="Смирнова Анна")])
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)

    client.post(
        "/cabinet/curator/profile",
        data={"first_name": "Анна", "last_name": "Смирнова"},
        follow_redirects=False,
    )

    assert db.query(Tag).filter(Tag.name == "1 Куратор").count() == 1
    assert db.query(Tag).filter(Tag.name == "Смирнова Анна").count() == 1


def test_rename_without_last_name(client, db, user_factory, session_factory):
    curator = _curator(db, user_factory)
    client.cookies.set("session_id", session_factory(curator).id)

    client.post(
        "/cabinet/curator/profile",
        data={"first_name": "Анна", "last_name": ""},
        follow_redirects=False,
    )

    db.expire_all()
    me = db.get(User, curator.id)
    assert (me.first_name, me.last_name, me.name) == ("Анна", None, "Анна")


def test_empty_or_long_name_is_refused(client, db, user_factory, session_factory):
    curator = _curator(db, user_factory)
    client.cookies.set("session_id", session_factory(curator).id)

    empty = client.post("/cabinet/curator/profile", data={"first_name": "  ", "last_name": ""})
    long = client.post("/cabinet/curator/profile", data={"first_name": "А" * 51, "last_name": ""})

    assert empty.status_code == 422
    assert "Напишите имя" in empty.text
    assert long.status_code == 422
    assert "Имя длиннее 50 символов" in long.text
    db.expire_all()
    assert db.get(User, curator.id).name == "Куратор 1"


def test_student_cannot_open_curator_profile(client, db, user_factory, session_factory):
    student = user_factory(vk_id=996_020, name="Ученик")
    client.cookies.set("session_id", session_factory(student).id)

    assert client.get("/cabinet/curator/profile", follow_redirects=False).status_code == 403
    assert client.post(
        "/cabinet/curator/profile", data={"first_name": "Хакер"}, follow_redirects=False,
    ).status_code == 403
