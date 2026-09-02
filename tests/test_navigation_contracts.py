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
        ("ученик", 301001, "/cabinet/learning"),
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
    bottom_nav = _html_between(resp.text, '<nav class="bottom-nav"', "</nav>")

    assert 'href="/cabinet/tracker"' in bottom_nav
    assert 'href="/cabinet/learning"' in bottom_nav
    assert 'href="/cabinet/portfolio"' in bottom_nav
    assert 'href="/cabinet/personal"' in bottom_nav
    assert 'href="/3dlab"' in bottom_nav

    # Статистика — заглушка «скоро», без рабочей ссылки.
    assert 'nav-soon-badge' in bottom_nav

    # Цикл пробника и загрузка пробника ушли из нижнего меню (роуты живы по прямым ссылкам,
    # но со страницы кабинета ученика на них уже есть контекстные ссылки вне нижнего меню).
    assert 'href="/cabinet/cycle"' not in bottom_nav
    assert 'href="/upload/mock-exam"' not in bottom_nav

    assert 'class="staff-aside"' not in resp.text
    assert 'href="/cabinet/students"' not in resp.text
    assert 'href="/cabinet/staff/students-review"' not in resp.text
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
    # Куратор использует staff-стиль навигации (сайдбар + нижний pill), но со СВОИМ
    # набором пунктов из curator_nav_items() — это не admin staff_nav.
    assert 'class="staff-aside"' in resp.text
    assert 'href="/cabinet/students"' in resp.text
    assert 'href="/cabinet/staff/students-review"' in resp.text
    assert 'href="/cabinet/curator/reports"' in resp.text
    assert 'href="/cabinet/students?tab=statistics"' in resp.text

    # Куратор НЕ получает admin-only / student-only пункты.
    assert 'href="/cabinet/admin/mock-check"' not in resp.text
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
    assert 'class="mobile-dashboard-logout"' in resp.text
    assert 'method="post" action="/logout"' in resp.text
    staff_nav = _html_between(resp.text, '<aside class="staff-aside">', "</aside>")

    assert 'class="staff-aside"' in staff_nav
    assert 'href="/cabinet/students"' in staff_nav
    assert 'href="/cabinet/staff/students-review"' in staff_nav
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


# ── Фаза 3 (2026-07-05): cabinet_feedback_detail.html / cabinet_cycle_calendar.html
# не использовали общий nav-конфиг — staff_nav дублировался для admin/SA (base.html
# уже подключает его для rank>=4) и куратор получал staff_nav вместо _curator_nav. ──

@pytest.mark.parametrize(
    ("role_name", "vk_id", "expect_curator_nav"),
    [
        ("куратор", 303001, True),
        ("модератор", 303002, False),
        ("админ", 303003, False),
        ("суперадмин", 303004, False),
    ],
)
def test_feedback_detail_exactly_one_nav_per_role(
    client, db, user_factory, session_factory, role_name, vk_id, expect_curator_nav
):
    from datetime import date
    from app.models.exam_cycle import ExamCycle
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    actor = user_factory(vk_id=vk_id, name="Nav Actor", role_name=role_name)
    student = user_factory(vk_id=vk_id + 1, name="Nav Student", role_name="ученик")
    if role_name == "куратор":
        student.curator_id = actor.id
        db.add(student)
        db.commit()
    cycle = ExamCycle(user_id=student.id, subject="Drawing", started_at=date(2026, 5, 10))
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026, filename="final.jpg", subject="Drawing",
        status="success", s3_url="https://example.test/final.jpg",
        is_final=True, cycle_id=cycle.id, attempt_number=1,
    )
    db.add(work)
    db.commit()

    _login_as(client, session_factory, actor)
    resp = client.get(f"/cabinet/curator/feedback/{cycle.id}")

    assert resp.status_code == 200
    assert resp.text.count('<aside class="staff-aside">') == 1
    if expect_curator_nav:
        assert 'aria-label="Меню куратора"' in resp.text
        assert 'aria-label="Меню персонала"' not in resp.text
    else:
        assert 'aria-label="Меню персонала"' in resp.text
        assert 'aria-label="Меню куратора"' not in resp.text


@pytest.mark.parametrize(
    ("role_name", "vk_id", "expect_curator_nav"),
    [
        ("куратор", 303011, True),
        ("модератор", 303012, False),
        ("админ", 303013, False),
        ("суперадмин", 303014, False),
    ],
)
def test_cycle_calendar_exactly_one_nav_per_role(
    client, db, user_factory, session_factory, role_name, vk_id, expect_curator_nav
):
    actor = user_factory(vk_id=vk_id, name="Cal Nav Actor", role_name=role_name)
    student = user_factory(vk_id=vk_id + 1, name="Cal Nav Student", role_name="ученик")
    if role_name == "куратор":
        student.curator_id = actor.id
        db.add(student)
        db.commit()

    _login_as(client, session_factory, actor)
    resp = client.get(f"/cabinet/staff/cycle/probnik/{student.id}")

    assert resp.status_code == 200
    assert resp.text.count('<aside class="staff-aside">') == 1
    if expect_curator_nav:
        assert 'aria-label="Меню куратора"' in resp.text
        assert 'aria-label="Меню персонала"' not in resp.text
    else:
        assert 'aria-label="Меню персонала"' in resp.text
        assert 'aria-label="Меню куратора"' not in resp.text
