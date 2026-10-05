"""Кнопка спрашивает то же правило, что сервер (шаг 2 плана
`plans/2026-10-04-apparchi-тонкие-доступы.md`, развилка 6).

Прод 04.10.2026: АОП открыт всей роли модераторов на «Смотреть», а экраны
программы, дайджеста, целей и видео рисовали им все кнопки правки — каждое
нажатие отвечало 403. Теперь шаблоны спрашивают `can(user, раздел)`: при
«Смотреть» кнопок записи в разметке нет, при «Менять» — есть.
"""
import re
from datetime import timedelta, timezone

import pytest

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.section_access import SectionAccessRule
from app.models.tracker import ScheduleDigest
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()


def _login_as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _markup(resp) -> str:
    """Разметка без скриптов: селекторы вида `[data-stage-new]` в JS — не кнопки."""
    return re.sub(r"<script\b.*?</script>", "", resp.text, flags=re.S)


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    topic = LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(TODAY)),
        ends_at=_utc(msk_midnight(TODAY + timedelta(days=5))),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _moderator(db, user_factory, vk_id, **levels):
    """Модератор с личными уровнями `{раздел: уровень}`."""
    moderator = user_factory(vk_id=vk_id, name="Модератор", role_name="модератор")
    db.add_all([
        SectionAccessRule(section_key=key, user_id=moderator.id, level=level)
        for key, level in levels.items()
    ])
    db.commit()
    return moderator


def _program_pages(db, owner):
    """{адрес: метка кнопки записи, которой при «Смотреть» быть не должно}."""
    cycle = _cycle(db, owner)
    digest = ScheduleDigest(title="Октябрь", year=2026, month=10, assign_to_all=True)
    db.add(digest)
    db.commit()
    future_day = (TODAY + timedelta(days=3)).isoformat()
    return {
        "/cabinet/staff/program/stages": "data-stage-new",
        "/cabinet/staff/program/cycles": "data-cycle-delete",
        f"/cabinet/staff/program/cycles/{cycle.id}": "data-open-form",
        f"/cabinet/staff/program/{future_day}": 'data-open-form="',
        "/cabinet/staff/digest": "data-digest-toggle",
        f"/cabinet/staff/digest/{digest.id}/events": "data-digest-cal",
        "/cabinet/staff/digest/types": "data-type-new",
        "/cabinet/staff/goals": "data-goal-toggle",
        "/cabinet/staff/tracker": "data-task-toggle",
        "/cabinet/admin/videos": 'id="video-upload-form"',
    }


@pytest.mark.parametrize("level", ["view", "edit"])
def test_program_write_buttons_follow_level(
    client, db, user_factory, session_factory, level,
):
    moderator = _moderator(db, user_factory, 990_401 if level == "view" else 990_402, program=level)
    pages = _program_pages(db, moderator)
    _login_as(client, session_factory, moderator)

    for path, marker in pages.items():
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert (marker in _markup(resp)) is (level == "edit"), (level, path)


def test_chief_teacher_keeps_program_buttons(client, db, user_factory, session_factory):
    """ГП в родном разделе — «Менять» без строк в базе."""
    chief = user_factory(vk_id=990_403, name="ГП", role_name="админ")
    pages = _program_pages(db, chief)
    _login_as(client, session_factory, chief)

    for path, marker in pages.items():
        assert marker in _markup(client.get(path)), path


def _students_can_edit_card(client) -> str:
    resp = client.get("/cabinet/students")
    assert resp.status_code == 200
    return "const CAN_SCORE      = true;" in resp.text


def test_student_card_buttons_hidden_for_moderator(client, db, user_factory, session_factory):
    """«Ученики» модератору положены на «Смотреть»: анкета, загрузка, удаление
    и балл в карточке отвечали ему 403 — кнопок нет."""
    student = user_factory(vk_id=990_404, name="Ученик", role_name="ученик")
    student.profile_completed = True
    moderator = _moderator(db, user_factory, 990_405)
    _login_as(client, session_factory, moderator)
    assert not _students_can_edit_card(client)


@pytest.mark.parametrize(("level", "expected"), [("view", False), ("edit", True)])
def test_student_card_buttons_follow_chief_level(
    client, db, user_factory, session_factory, level, expected,
):
    chief = user_factory(vk_id=990_406 if level == "view" else 990_407, name="ГП", role_name="админ")
    if level != "edit":
        db.add(SectionAccessRule(section_key="students", user_id=chief.id, level=level))
        db.commit()
    _login_as(client, session_factory, chief)
    assert _students_can_edit_card(client) is expected


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
    assert ('class="role-form"' in _markup(users)) is editable
    assert ("data-user-actions=" in _markup(users)) is editable

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
    assert ("bulkLookup()" in _markup(tags)) is editable

    assign = client.get("/cabinet/superadmin/assign-curator")
    assert assign.status_code == 200
    assert ("assign-curator-bulk" in _markup(assign)) is editable
