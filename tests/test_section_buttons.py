"""Кнопка спрашивает то же правило, что сервер (шаг 2 плана
`plans/2026-10-04-apparchi-тонкие-доступы.md`, развилка 6).

Прод 04.10.2026: АОП открыт всей роли модераторов на «Смотреть», а экраны
программы, дайджеста, целей и видео рисовали им все кнопки правки — каждое
нажатие отвечало 403. Теперь шаблоны спрашивают `can(user, раздел)`: при
«Смотреть» кнопок записи в разметке нет, при «Менять» — есть.
"""

import pytest

from app.models.section_access import SectionAccessRule


def _login_as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _moderator(db, user_factory, vk_id, **levels):
    """Модератор с личными уровнями `{раздел: уровень}`."""
    moderator = user_factory(vk_id=vk_id, name="Модератор", role_name="модератор")
    db.add_all([
        SectionAccessRule(section_key=key, user_id=moderator.id, level=level)
        for key, level in levels.items()
    ])
    db.commit()
    return moderator


def _students_can_edit_card(client, rendered) -> bool:
    """Кнопки правки в карточке ученика: решение роута, а не строка в скрипте."""
    assert client.get("/cabinet/students").status_code == 200
    return rendered("cabinet_students.html")["can_edit_card"]


def test_student_card_buttons_hidden_for_moderator(client, db, user_factory, session_factory, rendered):
    """«Ученики» модератору положены на «Смотреть»: анкета, загрузка, удаление
    и балл в карточке отвечали ему 403 — кнопок нет."""
    student = user_factory(vk_id=990_404, name="Ученик", role_name="ученик")
    student.profile_completed = True
    moderator = _moderator(db, user_factory, 990_405)
    _login_as(client, session_factory, moderator)
    assert not _students_can_edit_card(client, rendered)


@pytest.mark.parametrize(("level", "expected"), [("view", False), ("edit", True)])
def test_student_card_buttons_follow_chief_level(
    client, db, user_factory, session_factory, rendered, level, expected,
):
    chief = user_factory(vk_id=990_406 if level == "view" else 990_407, name="ГП", role_name="админ")
    if level != "edit":
        db.add(SectionAccessRule(section_key="students", user_id=chief.id, level=level))
        db.commit()
    _login_as(client, session_factory, chief)
    assert _students_can_edit_card(client, rendered) is expected


@pytest.mark.parametrize("level", ["view", "edit"])
def test_people_write_buttons_follow_level(
    client, db, user_factory, session_factory, level,
):
    student = user_factory(vk_id=990_410 if level == "view" else 990_411, name="Ученик", role_name="ученик")
    student.profile_completed = True
    db.commit()
    moderator = _moderator(
        db, user_factory, 990_412 if level == "view" else 990_413, people=level,
    )
    _login_as(client, session_factory, moderator)
    editable = level == "edit"

    users = client.get("/cabinet/superadmin/users?show_hidden=1")
    assert users.status_code == 200

    # Карточка ученика в «Людях» уводит в «Учеников» (05.10.2026), кнопки
    # правки там — блок «Управление» с флагами тех же действий `people:*`.
    card = client.get(f"/cabinet/superadmin/users/{student.id}", follow_redirects=False)
    assert card.status_code == 302
    assert card.headers["location"] == f"/cabinet/students?student={student.id}"
    manage = client.get(f"/cabinet/students/{student.id}/profile").json()["student"]["manage"]
    assert manage["can_edit"] is editable
    assert manage["can_login"] is editable

    tags = client.get("/cabinet/superadmin/tags")
    assert tags.status_code == 200

    assign = client.get("/cabinet/superadmin/assign-curator")
    assert assign.status_code == 200
