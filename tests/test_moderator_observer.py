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
        "/cabinet/staff/notifications",
        "/csrf",
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
    [
        "/cabinet/students", "/cabinet/archive", "/cabinet/superadmin/activity",
        "/cabinet/staff/notifications", "/csrf",
    ],
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


def test_moderator_refusal_leaves_log_line(moderator_client, caplog):
    """Отказ пишется в лог с адресом и причиной (04.10.2026): до этого девять
    403 подряд не оставили следа, и причину искали по базе."""
    with caplog.at_level("WARNING", logger="app.dependencies"):
        moderator_client.get("/cabinet/staff/point-a", follow_redirects=False)
    lines = [r.getMessage() for r in caplog.records if "Отказ в доступе" in r.getMessage()]
    assert lines and "GET /cabinet/staff/point-a" in lines[0]
    assert "белого списка модератора" in lines[0]


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
        for tab in ("profile", "portfolio", "tasks", "mock-exams", "statistics"):
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


def test_moderator_with_every_section_open_browses_without_writes(
    client, db, user_factory, session_factory,
):
    """Суперадмин может открыть модератору любой раздел сверх роли — на чтение
    (`section_access.moderator_may_read`, владелец 03.10.2026). Значит, ни
    один GET этих разделов не должен ничего писать, тот же довод, что выше."""
    from fastapi.routing import APIRoute
    from sqlalchemy import event

    from app.main import app
    from app.models.section_access import SectionAccessRule
    from app.services import section_access

    moderator = user_factory(vk_id=990_320, name="Модератор с разделами", role_name="модератор")
    student = user_factory(vk_id=990_321, name="Ученик", role_name="ученик")
    student.profile_completed = True
    db.commit()
    extra = [s.key for s in section_access.SECTIONS if "модератор" not in s.roles]
    db.add_all([
        SectionAccessRule(section_key=key, user_id=moderator.id, is_open=True) for key in extra
    ])
    db.commit()
    _login_as(client, session_factory, moderator)

    paths = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        # Вход в 3D-лабораторию выдаёт одноразовый SSO-код — это вход, а не
        # просмотр, и проверять его здесь не к чему.
        if route.path == "/cabinet/3dlab/enter" or "{" in route.path.replace("{student_id}", ""):
            continue
        path = route.path.replace("{student_id}", str(student.id))
        if set(section_access.section_owners("GET", path, {})) & set(extra):
            paths.append(path)
    assert "/cabinet/staff/program/cycles" in paths

    writes = []

    def _on_execute(conn, cursor, statement, *args):
        head = statement.lstrip().split(None, 2)
        if head and head[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            if not statement.lstrip().upper().startswith("UPDATE SESSIONS"):
                writes.append((current, statement.strip().splitlines()[0]))

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        for current in paths:
            resp = client.get(current, follow_redirects=False)
            assert resp.status_code < 500, current
            assert resp.status_code != 403 or "Модератору" not in resp.text, current
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)

    # Экран тегов дозаполняет теги из анкеты (`tags.ensure_profile_tags`):
    # запись идемпотентна, ту же сделал бы первый заход ГП, и отметок
    # «просмотрено» она не снимает — для наблюдателя безвредна.
    writes = [w for w in writes if w[0] != "/cabinet/superadmin/tags"]
    assert writes == []


# ── Личная галочка: модератор работает в разделе (владелец 04.10.2026) ─────────

def _digest_with_type(db):
    from app.models.tracker import ScheduleDigest
    from app.services.schedule_event_types import create_type

    digest = ScheduleDigest(title="Октябрь", year=2026, month=10, assign_to_all=True)
    db.add(digest)
    event_type = create_type(db, name="Занятие", color="violet", style="fill")
    db.commit()
    return digest, event_type


def _event_payload(type_id):
    return {
        "type_id": type_id, "title": "Эфир", "note": None,
        "starts_on": "2026-10-10", "ends_on": "2026-10-10",
        "meeting_url": None, "sort_order": 0,
    }


def test_moderator_with_personal_program_grant_adds_digest_event(
    client, db, user_factory, session_factory,
):
    """«Ей дать все права по АОП, что и у меня и ГП»: личная галочка АОП в
    карточке модератора открывает раздел на полную работу. Прод 04.10.2026:
    Александрия девять раз сохраняла событие дайджеста и получала 403."""
    from app.models.section_access import SectionAccessRule
    from app.models.tracker import ScheduleEvent

    moderator = user_factory(vk_id=990_330, name="Модератор АОП", role_name="модератор")
    db.add(SectionAccessRule(section_key="program", user_id=moderator.id, is_open=True))
    digest, event_type = _digest_with_type(db)
    _login_as(client, session_factory, moderator)

    resp = client.post(f"/cabinet/staff/digest/{digest.id}/events", json=_event_payload(event_type.id))
    assert resp.status_code == 200
    assert db.get(ScheduleEvent, resp.json()["event_id"]).title == "Эфир"
    # Работа только в АОП: соседние разделы ГП остаются закрытыми.
    assert client.get("/cabinet/staff/point-a").status_code == 403
    assert client.post("/cabinet/students/5/works/7/score").status_code == 403


def test_moderator_with_role_program_grant_still_only_reads(
    client, db, user_factory, session_factory,
):
    """Галочка на всю роль открывает АОП всем модераторам — только на просмотр."""
    from app.models.section_access import SectionAccessRule

    moderator = user_factory(vk_id=990_331, name="Модератор роли", role_name="модератор")
    db.add(SectionAccessRule(section_key="program", role_id=moderator.role_id, is_open=True))
    digest, event_type = _digest_with_type(db)
    _login_as(client, session_factory, moderator)

    assert client.get(f"/cabinet/staff/digest/{digest.id}/events").status_code == 200
    resp = client.post(f"/cabinet/staff/digest/{digest.id}/events", json=_event_payload(event_type.id))
    assert resp.status_code == 403


def test_personal_grant_policy_writes_only_inside_workable_sections():
    from app.services.section_access import moderator_may_use

    granted = frozenset({"program", "lab3d"})
    workable = frozenset({"program"})
    assert moderator_may_use("POST", "/cabinet/staff/digest/2/events", {}, granted, workable)
    assert moderator_may_use("POST", "/cabinet/upload-ticket-image", {}, granted, workable)
    assert not moderator_may_use("POST", "/cabinet/3dlab", {}, granted, workable)
    assert moderator_may_use("GET", "/cabinet/3dlab", {}, granted, workable)
    assert not moderator_may_use("POST", "/cabinet/staff/digest/2/events", {}, granted)
