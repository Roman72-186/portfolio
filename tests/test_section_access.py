"""Переключатели доступа к разделам (владелец 30.09.2026).

Суперадмин закрывает сотрудникам разделы — роли целиком или одному человеку.
Только сужение: потолок задаёт ранг. Каталог и проверка —
`app/services/section_access.py`, проверка на входе — `get_current_user`.
"""
import re

import pytest
from fastapi.routing import APIRoute

from app.models.audit_log import AuditLog
from app.models.section_access import SectionAccessRule
from app.services import section_access
from app.services.navigation import curator_nav_items, staff_nav_items
from app.services.section_access import (
    SECTION_CLOSED_DETAIL,
    blocked_section,
    closed_sections,
    save_role_matrix,
    section_owners,
)


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _close_for_role(db, actor, role_name, *keys):
    save_role_matrix(db, actor_id=actor.id, desired={role_name: {k: False for k in keys}})


def _personal(db, user, key, is_open):
    db.add(SectionAccessRule(section_key=key, user_id=user.id, is_open=is_open))
    db.commit()


@pytest.fixture()
def superadmin(user_factory):
    return user_factory(vk_id=990_500, name="СА", role_name="суперадмин")


@pytest.fixture()
def curator(user_factory):
    return user_factory(vk_id=990_501, name="Куратор", role_name="куратор")


@pytest.fixture()
def head(user_factory):
    return user_factory(vk_id=990_502, name="ГП", role_name="админ")


@pytest.fixture()
def moderator(user_factory):
    return user_factory(vk_id=990_503, name="Модератор", role_name="модератор")


# ── Каталог разделов ──────────────────────────────────────────────────────────

def _route_samples():
    from app.main import app

    samples = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = re.sub(r"\{[^}]*:path\}", "a/b", route.path)
        path = re.sub(r"\{iso\}", "2026-W40", path)
        path = re.sub(r"\{[a-z_]*id\}", "5", path)
        path = re.sub(r"\{[^}]*\}", "x", path)
        samples.append((route.methods, path))
    return samples


def test_every_rule_matches_a_live_route():
    """Переименовали адрес — правило перестало бы закрывать молча. Здесь краснеет."""
    samples = _route_samples()
    dead = [
        rule.pattern.pattern
        for rule in section_access._RULES
        if not any(
            rule.pattern.match(path) and (rule.methods is None or methods & rule.methods)
            for methods, path in samples
        )
    ]
    assert dead == []


def test_every_rule_owner_is_a_known_section():
    for rule in section_access._RULES:
        for key in rule.owners:
            assert key in section_access.SECTIONS_BY_KEY


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/cabinet"),
        ("GET", "/cabinet/curator"),
        ("GET", "/cabinet/admin-panel"),
        ("GET", "/cabinet/notifications/feed"),
        ("POST", "/cabinet/notifications/mark-read"),
        ("GET", "/cabinet/feedback/5"),
        ("GET", "/cabinet/curator/feedback/5"),
        ("GET", "/cabinet/staff/cycle/probnik/5"),
        ("GET", "/cabinet/staff/homework/submissions/3"),
        ("GET", "/cabinet/staff/task-block-submissions/3"),
        # Балл, доработка и удаление работы зовут «Пробники» и диалог ОС.
        ("POST", "/cabinet/students/5/works/7/score"),
        ("POST", "/cabinet/students/5/mock-exams/7/revision"),
        ("DELETE", "/cabinet/students/5/works/7"),
        ("POST", "/cabinet/admin/works/7/score"),
        # Плеер встроен в задания программы.
        ("GET", "/cabinet/videos/5/embed"),
        ("POST", "/cabinet/superadmin/impersonate/stop"),
        ("GET", "/cabinet/students-x"),
        ("GET", "/3dlab-x"),
    ],
)
def test_shared_addresses_belong_to_no_section(method, path):
    assert section_owners(method, path, {}) == ()


@pytest.mark.parametrize(
    ("method", "path", "query", "owners"),
    [
        ("GET", "/cabinet/students", {"tab": "statistics"}, ("statistics",)),
        ("GET", "/cabinet/students", {}, ("students",)),
        ("GET", "/cabinet/students", {"tab": "portfolio"}, ("students",)),
        ("GET", "/cabinet/students/5/profile", {}, ("students", "archive")),
        ("POST", "/cabinet/students/5/profile", {}, ("students",)),
        ("GET", "/cabinet/students/5/mock-exams", {}, ("students", "archive", "mock_check")),
        ("GET", "/cabinet/archive", {}, ("archive",)),
        ("GET", "/cabinet/staff/program/cycles/12/stats", {}, ("statistics",)),
        ("GET", "/cabinet/staff/program/cycles/12", {}, ("program",)),
        ("GET", "/cabinet/admin/videos", {}, ("program",)),
        ("GET", "/3dlab", {}, ("lab3d",)),
        ("POST", "/cabinet/superadmin/impersonate/9", {}, ("people",)),
        ("GET", "/cabinet/superadmin/users/9", {}, ("people",)),
    ],
)
def test_section_owners(method, path, query, owners):
    assert section_owners(method, path, query) == owners


def test_shared_card_is_closed_only_when_every_owner_is_closed():
    path = "/cabinet/students/5/profile"
    assert blocked_section("GET", path, {}, frozenset({"students"})) is None
    assert blocked_section("GET", path, {}, frozenset({"students", "archive"})) == "students"


# ── Правила: роль, личное, приоритет ──────────────────────────────────────────

def test_personal_rule_beats_role_rule(db, superadmin, curator):
    _close_for_role(db, superadmin, "куратор", "statistics", "reports")
    _personal(db, curator, "statistics", True)
    _personal(db, curator, "students", False)
    closed = closed_sections(db, user_id=curator.id, role_id=curator.role_id)
    assert closed == frozenset({"reports", "students"})


def test_unknown_section_key_in_db_is_ignored(db, curator):
    _personal(db, curator, "removed_section", False)
    assert closed_sections(db, user_id=curator.id, role_id=curator.role_id) == frozenset()


def test_role_matrix_skips_cells_the_form_did_not_send(db, superadmin, curator):
    _close_for_role(db, superadmin, "куратор", "reports")
    changes = save_role_matrix(db, actor_id=superadmin.id, desired={})
    assert changes == 0
    assert section_access.role_matrix(db)["куратор"]["reports"] is False


def test_role_matrix_ignores_sections_the_role_does_not_have(db, superadmin, curator):
    save_role_matrix(db, actor_id=superadmin.id, desired={"куратор": {"program": False}})
    assert db.query(SectionAccessRule).count() == 0


# ── Живые запросы ─────────────────────────────────────────────────────────────

def test_closed_statistics_tab_gives_403_but_list_stays(client, db, session_factory, superadmin, curator):
    _close_for_role(db, superadmin, "куратор", "statistics")
    _login(client, session_factory, curator)
    resp = client.get("/cabinet/students?tab=statistics", follow_redirects=False)
    assert resp.status_code == 403
    assert SECTION_CLOSED_DETAIL in resp.text
    # Заглушка не должна пугать сотрудника блокировкой аккаунта.
    assert "Раздел закрыт" in resp.text
    assert "Аккаунт заблокирован" not in resp.text
    assert client.get("/cabinet/students", follow_redirects=False).status_code == 200


def test_personal_open_reopens_for_one_curator(client, db, session_factory, superadmin, curator, user_factory):
    other = user_factory(vk_id=990_510, name="Второй куратор", role_name="куратор")
    _close_for_role(db, superadmin, "куратор", "reports")
    _personal(db, curator, "reports", True)
    _login(client, session_factory, curator)
    assert client.get("/cabinet/curator/reports", follow_redirects=False).status_code == 200
    _login(client, session_factory, other)
    assert client.get("/cabinet/curator/reports", follow_redirects=False).status_code == 403


def test_personal_open_does_not_lift_rank_ceiling(client, db, session_factory, curator):
    _personal(db, curator, "program", True)
    _login(client, session_factory, curator)
    resp = client.get("/cabinet/staff/program/cycles", follow_redirects=False)
    assert resp.status_code == 403
    assert SECTION_CLOSED_DETAIL not in resp.text


def test_head_closed_program_gets_403(client, db, session_factory, superadmin, head):
    _close_for_role(db, superadmin, "админ", "program")
    _login(client, session_factory, head)
    resp = client.get("/cabinet/staff/program/cycles", follow_redirects=False)
    assert resp.status_code == 403
    assert SECTION_CLOSED_DETAIL in resp.text


def test_superadmin_is_never_closed(client, db, session_factory, superadmin):
    _personal(db, superadmin, "people", False)
    _login(client, session_factory, superadmin)
    assert client.get("/cabinet/superadmin/users", follow_redirects=False).status_code == 200


def test_moderator_home_moves_to_first_open_section(client, db, session_factory, superadmin, moderator):
    _close_for_role(db, superadmin, "модератор", "students")
    _login(client, session_factory, moderator)
    assert client.get("/cabinet/students", follow_redirects=False).status_code == 403
    resp = client.get("/cabinet", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/archive"


def test_moderator_with_everything_closed_sees_reason(client, db, session_factory, superadmin, moderator):
    _close_for_role(db, superadmin, "модератор", "students", "archive", "statistics")
    _login(client, session_factory, moderator)
    resp = client.get("/cabinet", follow_redirects=False)
    assert resp.status_code == 403
    assert SECTION_CLOSED_DETAIL in resp.text


# ── Меню ──────────────────────────────────────────────────────────────────────

def test_staff_menu_hides_closed_sections():
    keys = [i.key for i in staff_nav_items(4, "админ", frozenset({"program", "archive"}))]
    assert "program" not in keys and "archive" not in keys
    assert "students" in keys


def test_moderator_menu_hides_closed_sections():
    keys = [i.key for i in staff_nav_items(4, "модератор", frozenset({"statistics"}))]
    assert keys == ["students", "archive"]


def test_curator_menu_hides_closed_sections():
    keys = [i.key for i in curator_nav_items(frozenset({"reports", "lab3d"}))]
    assert "reports" not in keys and "3dlab" not in keys
    assert "students" in keys


def test_access_menu_item_is_superadmin_only():
    assert "access" in [i.key for i in staff_nav_items(5, "суперадмин")]
    assert "access" not in [i.key for i in staff_nav_items(4, "админ")]


def test_curator_dashboard_drops_closed_tile(client, db, session_factory, superadmin, curator):
    _close_for_role(db, superadmin, "куратор", "reports")
    _login(client, session_factory, curator)
    resp = client.get("/cabinet/curator", follow_redirects=False)
    assert resp.status_code == 200
    assert 'href="/cabinet/curator/reports"' not in resp.text


# ── Экран «Доступы» и карточка ────────────────────────────────────────────────

def test_access_page_is_superadmin_only(client, session_factory, superadmin, head):
    _login(client, session_factory, head)
    assert client.get("/cabinet/superadmin/access", follow_redirects=False).status_code == 403
    _login(client, session_factory, superadmin)
    resp = client.get("/cabinet/superadmin/access", follow_redirects=False)
    assert resp.status_code == 200
    assert 'name="cell__curator__statistics"' in resp.text
    # У куратора нет программ по рангу — ячейки нет.
    assert 'name="cell__curator__program"' not in resp.text


def test_access_page_saves_matrix_and_audits(client, db, session_factory, superadmin, curator, head):
    _login(client, session_factory, superadmin)
    resp = client.post(
        "/cabinet/superadmin/access",
        data={
            "cell__curator__statistics": ["0"],
            "cell__head__program": ["0", "1"],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    matrix = section_access.role_matrix(db)
    assert matrix["куратор"]["statistics"] is False
    assert matrix["админ"]["program"] is True
    audit = db.query(AuditLog).filter(AuditLog.action == "section_access_role").all()
    assert len(audit) == 1


def test_user_card_block_and_save(client, db, session_factory, superadmin, curator, head):
    _login(client, session_factory, head)
    assert 'name="section__students"' not in client.get(f"/cabinet/superadmin/users/{curator.id}").text
    assert client.post(
        f"/cabinet/superadmin/users/{curator.id}/access", data={"section__students": "closed"},
        follow_redirects=False,
    ).status_code == 403

    _login(client, session_factory, superadmin)
    assert 'name="section__students"' in client.get(f"/cabinet/superadmin/users/{curator.id}").text
    resp = client.post(
        f"/cabinet/superadmin/users/{curator.id}/access",
        data={"section__students": "closed", "section__reports": "role"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert closed_sections(db, user_id=curator.id, role_id=curator.role_id) == frozenset({"students"})
    assert db.query(AuditLog).filter(AuditLog.action == "section_access_user").count() == 1


def test_user_rules_refuse_student_target(client, session_factory, superadmin, user_factory):
    student = user_factory(vk_id=990_520, name="Ученик")
    _login(client, session_factory, superadmin)
    resp = client.post(
        f"/cabinet/superadmin/users/{student.id}/access", data={"section__students": "closed"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
