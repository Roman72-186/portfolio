"""Действия внутри разделов (шаг 4 плана `plans/2026-10-04-apparchi-тонкие-доступы.md`).

Действие — отдельная галочка поверх уровня раздела: роль, логин и пароль,
вход «глазами», блокировка, тариф и теги в «Людях», плюс бывшие «только
суперадмин» (архив, полное удаление, создание аккаунтов, удаление видео,
участники гостевого пробника, диалоги ОС, месяцы портфолио). Без строк в базе
права прежние — это сторожит `tests/test_section_levels.py`. Здесь — что
галочка действительно открывает и закрывает и что потолок «только ниже своей
роли» держится и с открытым действием.
"""
import re

import pytest
from fastapi.routing import APIRoute

from app.models.role import Role
from app.models.section_access import SectionAccessRule
from app.models.user import User
from app.services import section_access


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _grant(db, user, key, level="edit"):
    db.add(SectionAccessRule(section_key=key, user_id=user.id, level=level))
    db.commit()


@pytest.fixture()
def head(user_factory):
    return user_factory(vk_id=990_700, name="ГП", role_name="админ")


@pytest.fixture()
def curator(user_factory):
    return user_factory(vk_id=990_701, name="Куратор", role_name="куратор")


@pytest.fixture()
def student(user_factory):
    return user_factory(vk_id=990_702, name="Ученик", role_name="ученик")


# ── Каталог ───────────────────────────────────────────────────────────────────

def test_every_action_rule_matches_a_live_route():
    """Переименовали адрес — галочка перестала бы его держать молча."""
    from app.main import app

    samples = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            samples.append((route.methods, re.sub(r"\{[^}]*\}", "5", route.path)))
    dead = [
        (key, pattern.pattern)
        for key, pattern, methods in section_access._ACTION_RULES
        if not any(pattern.match(path) and route_methods & methods for route_methods, path in samples)
    ]
    assert dead == []


def test_every_action_has_a_rule_and_a_known_section():
    keys = {key for key, _, _ in section_access._ACTION_RULES}
    for action in section_access.ACTIONS:
        assert action.key in keys
        assert action.section is None or action.section in section_access.SECTIONS_BY_KEY


# ── Действия «Людей», которые ГП делает сегодня ───────────────────────────────

def test_head_loses_role_change_when_action_is_off(client, db, session_factory, head, student, role_factory):
    curator_role = role_factory("куратор", 2)
    _grant(db, head, "people:role", "none")
    _login(client, session_factory, head)
    resp = client.post(
        f"/cabinet/superadmin/users/{student.id}/role", data={"role_id": str(curator_role.id)},
        headers={"Accept": "application/json"}, follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == section_access.ACTION_CLOSED_DETAIL
    db.refresh(student)
    assert student.role.name == "ученик"


def test_head_role_ceiling_survives_open_action(client, db, session_factory, head, student, role_factory):
    """Открытое действие не снимает потолок: ГП не назначит ГП и суперадмина."""
    for name, rank in (("админ", 4), ("суперадмин", 5)):
        role = role_factory(name, rank)
        _login(client, session_factory, head)
        client.post(
            f"/cabinet/superadmin/users/{student.id}/role", data={"role_id": str(role.id)},
            follow_redirects=False,
        )
        db.refresh(student)
        assert student.role.name == "ученик", name


def test_curator_with_block_action_blocks_a_student(client, db, session_factory, curator, student):
    """Сверх роли действие поднимает ранг запроса до ГП, и сервис удаления и
    блокировки судит по нему, а не по роли в базе."""
    _login(client, session_factory, curator)
    url = f"/cabinet/superadmin/users/{student.id}/toggle-active"
    assert client.post(url, follow_redirects=False).status_code == 403

    _grant(db, curator, "people:block")
    resp = client.post(url, headers={"Accept": "application/json"}, follow_redirects=False)
    assert resp.status_code == 200
    db.refresh(student)
    assert student.is_active is False


def test_curator_impersonates_student_but_not_head(client, db, session_factory, curator, student, head):
    _grant(db, curator, "people:impersonate")
    _login(client, session_factory, curator)
    assert client.post(
        f"/cabinet/superadmin/impersonate/{head.id}", follow_redirects=False,
    ).status_code == 403
    assert client.post(
        f"/cabinet/superadmin/impersonate/{student.id}", follow_redirects=False,
    ).status_code == 303


# ── Бывшие «только суперадмин» ───────────────────────────────────────────────

def test_head_archives_only_with_action(client, db, session_factory, head, student):
    _login(client, session_factory, head)
    url = f"/cabinet/superadmin/users/{student.id}/archive"
    assert client.post(url, follow_redirects=False).status_code == 403
    db.refresh(student)
    assert student.archived_at is None

    _grant(db, head, "people:archive")
    assert client.post(url, follow_redirects=False).status_code == 303
    db.refresh(student)
    assert student.archived_at is not None


def test_head_hard_deletes_student_only_with_action(client, db, session_factory, head, student):
    _login(client, session_factory, head)
    url = f"/cabinet/superadmin/users/{student.id}/hard-delete"
    data = {"confirm_name": str(student.id)}
    assert client.post(url, data=data, follow_redirects=False).status_code == 403

    student_id = student.id
    _grant(db, head, "people:hard_delete")
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(User, student_id) is None


def test_hard_delete_still_refuses_staff(client, db, session_factory, head, curator):
    _grant(db, head, "people:hard_delete")
    _login(client, session_factory, head)
    resp = client.post(
        f"/cabinet/superadmin/users/{curator.id}/hard-delete",
        data={"confirm_name": str(curator.id)}, follow_redirects=False,
    )
    assert resp.status_code == 400


def test_head_creates_staff_only_below_own_rank(client, db, session_factory, head, role_factory):
    curator_role = role_factory("куратор", 2)
    head_role = db.query(Role).filter(Role.name == "админ").one()
    _login(client, session_factory, head)
    assert client.get("/cabinet/superadmin/create-staff", follow_redirects=False).status_code == 403

    _grant(db, head, "people:create")
    assert client.get("/cabinet/superadmin/create-staff", follow_redirects=False).status_code == 200
    client.post(
        "/cabinet/superadmin/users/create-staff",
        data={"first_name": "Равный", "role_id": str(head_role.id)}, follow_redirects=False,
    )
    assert db.query(User).filter(User.first_name == "Равный").count() == 0
    client.post(
        "/cabinet/superadmin/users/create-staff",
        data={"first_name": "Новый", "role_id": str(curator_role.id)}, follow_redirects=False,
    )
    assert db.query(User).filter(User.first_name == "Новый").count() == 1


@pytest.mark.parametrize(
    ("key", "method", "path"),
    [
        ("program:video_delete", "POST", "/cabinet/admin/videos/987654/delete"),
        ("guest_exam:participants", "POST", "/cabinet/staff/guest-exam/participants/987654/delete"),
        ("feedback:dialogs", "POST", "/cabinet/superadmin/feedback/987654/reopen"),
        ("students:portfolio_months", "PATCH", "/cabinet/students/987654/portfolio/month"),
    ],
)
def test_superadmin_only_actions_open_with_action(
    client, db, session_factory, head, key, method, path,
):
    """Без действия — отказ доступа; с ним запрос доходит до обработчика
    (несуществующий объект — уже его ответ, не 403)."""
    _login(client, session_factory, head)
    kwargs = {"json": {"confirmation": "x", "month": "01", "year": 2026}}
    assert client.request(method, path, follow_redirects=False, **kwargs).status_code == 403

    _grant(db, head, key)
    resp = client.request(method, path, follow_redirects=False, **kwargs)
    assert resp.status_code != 403, resp.text


# ── Кнопка = сервер ──────────────────────────────────────────────────────────

def test_users_list_buttons_follow_actions(client, db, session_factory, head, student):
    _login(client, session_factory, head)
    page = client.get("/cabinet/superadmin/users?show_hidden=1").text
    assert f'/cabinet/superadmin/users/{student.id}/role' in page
    assert f'/cabinet/superadmin/users/{student.id}/archive' not in page

    _grant(db, head, "people:role", "none")
    _grant(db, head, "people:archive")
    page = client.get("/cabinet/superadmin/users?show_hidden=1").text
    assert f'/cabinet/superadmin/users/{student.id}/role' not in page
    assert f'/cabinet/superadmin/users/{student.id}/archive' in page


# ── Экран «Доступы» ───────────────────────────────────────────────────────────

@pytest.fixture()
def superadmin(user_factory):
    return user_factory(vk_id=990_710, name="СА", role_name="суперадмин")


def test_access_screen_lists_actions_under_sections(client, session_factory, superadmin, head):
    _login(client, session_factory, superadmin)
    page = client.get("/cabinet/superadmin/access").text
    assert 'name="cell__head__people:role" value="edit"' in page
    assert 'data-follows="cell__head__people"' in page
    # За разделом идут пять обычных действий «Людей», бывшие «только
    # суперадмин» — нет.
    assert page.count('data-follows="cell__head__people"') == 5
    assert "Вне разделов" in page
    assert 'name="cell__curator__feedback:dialogs"' in page


def test_access_screen_stores_only_overrides(client, db, session_factory, superadmin, head):
    _login(client, session_factory, superadmin)

    def save(**cells):
        data = {}
        for key, checked in cells.items():
            data[f"cell__head__{key}"] = ["none", "edit"] if checked else ["none"]
        return client.post("/cabinet/superadmin/access", data=data, follow_redirects=False)

    def stored():
        return {
            r.section_key: r.level
            for r in db.query(SectionAccessRule).filter(SectionAccessRule.role_id == head.role_id)
        }

    # ГП снимают смену роли и дают архив — две строки.
    assert save(**{"people:role": False, "people:archive": True}).status_code == 303
    assert stored() == {"people:role": "none", "people:archive": "edit"}
    levels = section_access.role_levels(db)["админ"]
    assert levels["people:role"] == "none" and levels["people:archive"] == "edit"
    # Вернули как было — строки уходят.
    save(**{"people:role": True, "people:archive": False})
    assert stored() == {}


def test_lowering_section_closes_following_actions(db, superadmin, head):
    """«Люди» у ГП на «Смотреть» — обычные действия без своих строк закрыты."""
    section_access.save_role_levels(db, actor_id=superadmin.id, desired={"админ": {"people": "view"}})
    levels = section_access.role_levels(db)["админ"]
    assert levels["people:role"] == "none"
    assert levels["people:students"] == "none"


def test_card_saves_personal_action(client, db, session_factory, superadmin, curator):
    _login(client, session_factory, superadmin)
    page = client.get(f"/cabinet/superadmin/users/{curator.id}").text
    assert 'name="section__people:impersonate"' in page
    client.post(
        f"/cabinet/superadmin/users/{curator.id}/access",
        data={"section__people:impersonate": "edit"}, follow_redirects=False,
    )
    row = db.query(SectionAccessRule).filter_by(user_id=curator.id).one()
    assert (row.section_key, row.level) == ("people:impersonate", "edit")
