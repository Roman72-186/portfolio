"""Contract tests for role-specific cabinet redirects and navigation.

These tests intentionally describe the current behavior before refactoring.
They guard against accidentally turning different role menus into one shared
list or changing the role dispatcher while extracting navigation helpers.
"""


import pytest


def _login_as(client, session_factory, user):
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)


@pytest.mark.parametrize(
    ("role_name", "vk_id", "expected_location"),
    [
        ("ученик", 301001, "/cabinet/learning"),
        ("куратор", 301002, "/cabinet/curator"),
        ("модератор", 301003, "/cabinet/students"),
        ("админ", 301004, "/cabinet/admin-panel"),
        ("суперадмин", 301005, "/cabinet/superadmin"),
    ],
)
def test_cabinet_dispatcher_keeps_current_role_redirects(
    client,
    user_factory,
    session_factory,
    role_name,
    vk_id,
    expected_location,
):
    user = user_factory(vk_id=vk_id, role_name=role_name)
    _login_as(client, session_factory, user)

    resp = client.get("/cabinet", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == expected_location


def test_moderator_lands_on_students_and_sees_observer_menu(
    client,
    user_factory,
    session_factory,
):
    """Модератор — наблюдатель (решение владельца 28.09.2026): дом —
    «Ученики», в меню только «Ученики», архив и статистика."""
    user = user_factory(vk_id=302005, role_name="модератор")
    _login_as(client, session_factory, user)

    resp = client.get("/cabinet", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/students"

    student_panel = client.get("/cabinet/students", follow_redirects=False)
    assert student_panel.status_code == 200
