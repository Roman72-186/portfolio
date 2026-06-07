"""Contract tests for role-specific cabinet redirects and navigation.

These tests intentionally describe the current behavior before refactoring.
They guard against accidentally turning different role menus into one shared
list or changing the role dispatcher while extracting navigation helpers.
"""

import pytest


def _login_as(client, session_factory, user):
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)


def _html_between(text: str, start: str, end: str) -> str:
    start_at = text.index(start)
    end_at = text.index(end, start_at)
    return text[start_at:end_at]


@pytest.mark.parametrize(
    ("role_name", "vk_id", "expected_location"),
    [
        ("ученик", 301001, "/cabinet/student"),
        ("куратор", 301002, "/cabinet/curator"),
        ("модератор", 301003, "/cabinet/student"),
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


def test_student_cabinet_uses_student_bottom_nav_only(
    client,
    user_factory,
    session_factory,
):
    user = user_factory(vk_id=302001, role_name="ученик")
    _login_as(client, session_factory, user)

    resp = client.get("/cabinet/student")

    assert resp.status_code == 200
    assert 'class="bottom-nav"' in resp.text
    assert 'href="/cabinet/student"' in resp.text
    assert 'href="/cabinet/portfolio"' in resp.text
    assert 'href="/cabinet/cycle"' in resp.text
    assert 'href="/upload/mock-exam"' in resp.text
    assert 'href="/3dlab"' in resp.text

    assert 'class="staff-aside"' not in resp.text
    assert 'href="/cabinet/students"' not in resp.text
    assert 'href="/cabinet/staff/cycles"' not in resp.text
    assert 'href="/cabinet/curator/reports"' not in resp.text


def test_curator_reports_use_curator_nav_not_admin_staff_nav(
    client,
    user_factory,
    session_factory,
):
    user = user_factory(vk_id=302002, role_name="куратор")
    _login_as(client, session_factory, user)

    resp = client.get("/cabinet/curator/reports")

    assert resp.status_code == 200
    assert 'class="curator-nav"' in resp.text
    assert 'href="/cabinet/students"' in resp.text
    assert 'href="/cabinet/curator/reports"' in resp.text
    assert 'href="/cabinet/students?tab=statistics"' in resp.text

    assert 'class="staff-aside"' not in resp.text
    assert 'href="/cabinet/staff/cycles"' not in resp.text
    assert 'href="/cabinet/portfolio"' not in resp.text
    assert 'href="/upload/mock-exam"' not in resp.text


@pytest.mark.parametrize(
    ("role_name", "vk_id", "dashboard_path"),
    [
        ("админ", 302003, "/cabinet/admin-panel"),
        ("суперадмин", 302004, "/cabinet/superadmin"),
    ],
)
def test_admin_and_superadmin_keep_staff_nav_contract(
    client,
    user_factory,
    session_factory,
    role_name,
    vk_id,
    dashboard_path,
):
    user = user_factory(vk_id=vk_id, role_name=role_name)
    _login_as(client, session_factory, user)

    resp = client.get(dashboard_path)

    assert resp.status_code == 200
    staff_nav = _html_between(resp.text, '<aside class="staff-aside">', "</aside>")

    assert 'class="staff-aside"' in staff_nav
    assert 'href="/cabinet/students"' in staff_nav
    assert 'href="/cabinet/staff/cycles"' in staff_nav
    assert 'href="/3dlab"' in staff_nav
    assert 'href="/cabinet/curator/reports"' in staff_nav

    assert 'href="/cabinet/admin/mock-check"' not in staff_nav
    assert 'href="/cabinet/portfolio"' not in staff_nav
    assert 'href="/upload/mock-exam"' not in staff_nav


def test_moderator_keeps_current_student_redirect_but_no_student_panel_access(
    client,
    user_factory,
    session_factory,
):
    user = user_factory(vk_id=302005, role_name="модератор")
    _login_as(client, session_factory, user)

    resp = client.get("/cabinet", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/student"

    student_panel = client.get("/cabinet/students", follow_redirects=False)
    assert student_panel.status_code == 403
