"""Модератор — наблюдатель (решение владельца 28.09.2026).

Видит «Учеников» с архивом и статистику, больше ничего и ничего не меняет.
Уровень у него как у ГП (`effective_role_rank` = 4), доступ режет белый список
`rbac.py::is_moderator_request_allowed`, который вызывается из
`get_current_user`.
"""

import pytest

from app.services.rbac import is_moderator_request_allowed


def _login_as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


# ── Белый список сам по себе ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    [
        "/cabinet",
        "/cabinet/students",
        "/cabinet/students/5/profile",
        "/cabinet/students/5/legacy-portfolio",
        "/cabinet/archive",
        "/cabinet/superadmin/activity",
        "/cabinet/superadmin/stats",
        "/cabinet/superadmin/stats/export",
        "/cabinet/admin/registration-stats.csv",
        "/cabinet/staff/program/cycles/12/stats",
        "/cabinet/notifications/feed",
        "/static/css/base.css",
    ],
)
def test_policy_opens_reading_of_observer_pages(path):
    assert is_moderator_request_allowed("GET", path)


@pytest.mark.parametrize(
    "path",
    [
        "/cabinet/admin-panel",  # `/cabinet` открыт только точным совпадением
        "/cabinet/students-review",  # граница по сегменту, не по началу строки
        "/cabinet/staff/students-review",
        "/cabinet/staff/program/cycles",
        "/cabinet/staff/program/cycles/12",
        "/cabinet/staff/point-a",
        "/cabinet/superadmin/users",
        "/cabinet/superadmin/registration-stats.csv",
        "/cabinet/curator/feedback/1",
        "/cabinet/curator/reports",
        "/cabinet/admin/mock-check",
        "/3dlab",
    ],
)
def test_policy_closes_everything_else(path):
    assert not is_moderator_request_allowed("GET", path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/cabinet/students/5/profile"),
        ("POST", "/cabinet/students/5/works/7/score"),
        ("DELETE", "/cabinet/students/5/works/7"),
        ("PATCH", "/cabinet/students/5/portfolio/month"),
        ("POST", "/cabinet/superadmin/activity"),
    ],
)
def test_policy_closes_any_change_inside_open_sections(method, path):
    assert not is_moderator_request_allowed(method, path)


def test_policy_keeps_logout_and_own_notifications():
    assert is_moderator_request_allowed("POST", "/logout")
    assert is_moderator_request_allowed("POST", "/cabinet/notifications/mark-read")


# ── Живые запросы через get_current_user ─────────────────────────────────────

@pytest.fixture()
def moderator_client(client, user_factory, session_factory):
    moderator = user_factory(vk_id=990_301, name="Модератор", role_name="модератор")
    _login_as(client, session_factory, moderator)
    return client


@pytest.mark.parametrize(
    "path",
    ["/cabinet/students", "/cabinet/archive", "/cabinet/superadmin/activity"],
)
def test_moderator_opens_observer_pages(moderator_client, path):
    resp = moderator_client.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/cabinet/admin-panel",
        "/cabinet/staff/program/cycles",
        "/cabinet/staff/students-review",
        "/cabinet/superadmin/users",
        "/cabinet/staff/point-a",
    ],
)
def test_moderator_gets_403_outside_whitelist(moderator_client, path):
    resp = moderator_client.get(path, follow_redirects=False)
    assert resp.status_code == 403
    assert "Модератору открыты только" in resp.text


def test_moderator_sees_all_students_but_cannot_score(
    moderator_client, db, user_factory,
):
    """Список — вся школа, как у ГП; оценка работы — отказ."""
    from app.models.work import Work

    student = user_factory(vk_id=990_302, name="Ученик Школы", role_name="ученик")
    student.profile_completed = True
    db.commit()
    work = Work(
        user_id=student.id, work_type="portfolio", month="09", year=2026,
        filename="w.jpg", status="success", s3_url="https://example.test/w.jpg",
    )
    db.add(work)
    db.commit()

    profile = moderator_client.get(
        f"/cabinet/students/{student.id}/profile",
        headers={"Accept": "application/json"},
    )
    assert profile.status_code == 200

    score = moderator_client.post(
        f"/cabinet/students/{student.id}/works/{work.id}/score",
        data={"score": "80"},
        headers={"Accept": "application/json"},
    )
    assert score.status_code == 403
    db.refresh(work)
    assert work.score is None


def test_moderator_browsing_writes_nothing(moderator_client, db, user_factory):
    """Белый список открывает модератору любые GET — значит, ни один из них
    не должен ничего писать: иначе наблюдатель снимал бы у преподавателя
    отметки «не просмотрено» и «не прочитано». Продление своей сессии
    (`UPDATE sessions`) — не в счёт."""
    from datetime import date

    from sqlalchemy import event

    from app.models.exam_cycle import ExamCycle
    from app.models.work import WORK_TYPE_MOCK_EXAM, Work

    curator = user_factory(vk_id=990_310, name="Чужой куратор", role_name="куратор")
    student = user_factory(vk_id=990_311, name="Ученик Под Наблюдением", role_name="ученик")
    student.profile_completed = True
    student.curator_id = curator.id
    db.commit()
    cycle = ExamCycle(user_id=student.id, subject="Drawing", started_at=date(2026, 9, 1))
    db.add(cycle)
    db.commit()
    db.add_all([
        Work(user_id=student.id, work_type="portfolio", month="09", year=2026,
             filename="p.jpg", status="success", s3_url="https://example.test/p.jpg"),
        Work(user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="09", year=2026,
             filename="m.jpg", subject="Drawing", status="success",
             s3_url="https://example.test/m.jpg", is_final=True, cycle_id=cycle.id,
             attempt_number=1),
    ])
    db.commit()

    writes = []

    def _on_execute(conn, cursor, statement, *args):
        head = statement.lstrip().split(None, 2)
        if head and head[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            if not statement.lstrip().upper().startswith("UPDATE SESSIONS"):
                writes.append(statement.strip().splitlines()[0])

    json_headers = {"Accept": "application/json"}
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        for path in [
            "/cabinet/students",
            f"/cabinet/students?student={student.id}",
            "/cabinet/archive",
            "/cabinet/superadmin/activity",
            "/cabinet/superadmin/stats",
            "/cabinet/notifications/feed",
        ]:
            assert moderator_client.get(path).status_code == 200, path
        for tab in ("profile", "portfolio", "mock-exams", "statistics", "retakes", "cycles"):
            path = f"/cabinet/students/{student.id}/{tab}"
            assert moderator_client.get(path, headers=json_headers).status_code == 200, path
        moderator_client.get(f"/cabinet/students/{student.id}/legacy-portfolio")
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)

    assert writes == []


def test_chief_teacher_keeps_full_access(client, user_factory, session_factory):
    """Белый список держит только модератора: ГП открывает свои разделы как раньше."""
    chief = user_factory(vk_id=990_303, name="ГП", role_name="админ")
    _login_as(client, session_factory, chief)

    assert client.get("/cabinet/admin-panel").status_code == 200
    assert client.get("/cabinet/staff/students-review").status_code == 200
