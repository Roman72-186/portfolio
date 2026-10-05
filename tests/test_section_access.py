"""Переключатели доступа к разделам (владелец 30.09 и 03.10.2026).

Суперадмин закрывает и открывает сотрудникам разделы — роли целиком или одному
человеку, в том числе сверх роли. Каталог и проверка —
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
    judge_request,
    resolve_levels,
    save_role_levels,
    section_owners,
)


def closed_sections(db, *, user_id, role_id):
    from app.models.role import Role

    role = db.get(Role, role_id)
    levels = resolve_levels(db, user_id=user_id, role_id=role_id, role_name=role.name)
    return section_access.closed_sections(levels, role.name)


def granted_sections(db, user):
    levels = resolve_levels(
        db, user_id=user.id, role_id=user.role_id, role_name=user.role.name,
    )
    return section_access.granted_sections(levels, user.role.name)


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _close_for_role(db, actor, role_name, *keys):
    save_role_levels(db, actor_id=actor.id, desired={role_name: {k: "none" for k in keys}})


def _open_for_role(db, actor, role_name, *keys, level="edit"):
    save_role_levels(db, actor_id=actor.id, desired={role_name: {k: level for k in keys}})


def _personal(db, user, key, level):
    db.add(SectionAccessRule(section_key=key, user_id=user.id, level=level))
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
    head = {s.key: "edit" for s in section_access.SECTIONS}
    assert judge_request("GET", path, {}, {**head, "students": "none"}, "админ").refusal is None
    refused = judge_request("GET", path, {}, {**head, "students": "none", "archive": "none"}, "админ")
    assert refused.refusal == SECTION_CLOSED_DETAIL
    assert refused.refused_section == "students"


# ── Правила: роль, личное, приоритет ──────────────────────────────────────────

def test_personal_rule_beats_role_rule(db, superadmin, curator):
    _close_for_role(db, superadmin, "куратор", "statistics", "reports")
    _personal(db, curator, "statistics", "edit")
    _personal(db, curator, "students", "none")
    closed = closed_sections(db, user_id=curator.id, role_id=curator.role_id)
    assert closed == frozenset({"reports", "students"})


def test_unknown_section_key_in_db_is_ignored(db, curator):
    _personal(db, curator, "removed_section", "none")
    assert closed_sections(db, user_id=curator.id, role_id=curator.role_id) == frozenset()


def test_role_levels_skip_cells_the_form_did_not_send(db, superadmin, curator):
    _close_for_role(db, superadmin, "куратор", "reports")
    changes = save_role_levels(db, actor_id=superadmin.id, desired={})
    assert changes == 0
    assert section_access.role_levels(db)["куратор"]["reports"] == "none"


def test_role_levels_ignore_unknown_level(db, superadmin, curator):
    assert save_role_levels(
        db, actor_id=superadmin.id, desired={"куратор": {"reports": "full"}},
    ) == 0
    assert section_access.role_levels(db)["куратор"]["reports"] == "edit"


def test_view_only_section_cannot_be_set_to_edit(db, superadmin, curator):
    """У архива нет адресов на запись — «Менять» сохраняется как «Смотреть»."""
    _open_for_role(db, superadmin, "куратор", "archive", level="edit")
    assert section_access.role_levels(db)["куратор"]["archive"] == "view"


def test_role_matrix_stores_only_differences_from_rank(db, superadmin, curator):
    """Закрыть то, чего у роли и так нет, — ничего не записать. Открыть сверх
    роли и вернуть обратно — строка появляется и исчезает."""
    _close_for_role(db, superadmin, "куратор", "program")
    assert db.query(SectionAccessRule).count() == 0
    _open_for_role(db, superadmin, "куратор", "program")
    assert section_access.role_levels(db)["куратор"]["program"] == "edit"
    assert db.query(SectionAccessRule).count() == 1
    _close_for_role(db, superadmin, "куратор", "program")
    assert db.query(SectionAccessRule).count() == 0


def test_every_role_has_every_section_in_matrix(db):
    matrix = section_access.role_levels(db)
    for role in section_access.CONFIGURABLE_ROLES:
        assert set(matrix[role]) == set(section_access.SECTIONS_BY_KEY) | set(section_access.ACTIONS_BY_KEY)


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
    _personal(db, curator, "reports", "edit")
    _login(client, session_factory, curator)
    assert client.get("/cabinet/curator/reports", follow_redirects=False).status_code == 200
    _login(client, session_factory, other)
    assert client.get("/cabinet/curator/reports", follow_redirects=False).status_code == 403


def test_curator_without_program_stays_at_rank_ceiling(client, session_factory, curator):
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
    _personal(db, superadmin, "people", "none")
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
    # С 03.10.2026 ячейка есть у каждой роли, в том числе сверх ранга.
    assert 'name="cell__curator__program"' in resp.text
    assert 'name="cell__moderator__people"' in resp.text
    # С 04.10.2026 в ячейке уровни; у раздела без записи их два.
    assert 'name="cell__head__program" value="edit"' in resp.text
    assert 'name="cell__head__archive" value="edit"' not in resp.text
    assert 'name="cell__head__archive" value="view"' in resp.text
    # «Люди и доступы» сверх роли просят подтверждения, у ГП — родной раздел.
    # «Смотреть» и «Менять» у куратора и модератора — четыре кнопки.
    assert 'data-risky-what="«Люди и доступы» роли «Куратор»"' in resp.text
    assert resp.text.count('data-risky-what="«Люди и доступы»') == 4


def test_access_page_saves_matrix_and_audits(client, db, session_factory, superadmin, curator, head):
    _login(client, session_factory, superadmin)
    resp = client.post(
        "/cabinet/superadmin/access",
        data={
            "cell__curator__statistics": "none",
            "cell__head__program": "edit",
            "cell__moderator__program": "view",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    levels = section_access.role_levels(db)
    assert levels["куратор"]["statistics"] == "none"
    assert levels["админ"]["program"] == "edit"
    assert levels["модератор"]["program"] == "view"
    audit = db.query(AuditLog).filter(AuditLog.action == "section_access_role").all()
    assert len(audit) == 2


def test_user_card_block_and_save(client, db, session_factory, superadmin, curator, head):
    _login(client, session_factory, head)
    assert 'name="section__students"' not in client.get(f"/cabinet/superadmin/users/{curator.id}").text
    assert client.post(
        f"/cabinet/superadmin/users/{curator.id}/access", data={"section__students": "none"},
        follow_redirects=False,
    ).status_code == 403

    _login(client, session_factory, superadmin)
    assert 'name="section__students"' in client.get(f"/cabinet/superadmin/users/{curator.id}").text
    resp = client.post(
        f"/cabinet/superadmin/users/{curator.id}/access",
        data={"section__students": "none", "section__reports": "role"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert closed_sections(db, user_id=curator.id, role_id=curator.role_id) == frozenset({"students"})
    assert db.query(AuditLog).filter(AuditLog.action == "section_access_user").count() == 1


def test_user_rules_refuse_student_target(client, session_factory, superadmin, user_factory):
    student = user_factory(vk_id=990_520, name="Ученик")
    _login(client, session_factory, superadmin)
    resp = client.post(
        f"/cabinet/superadmin/users/{student.id}/access", data={"section__students": "none"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


# ── Личный доступ сверх роли: архив куратору (владелец 30.09.2026) ────────────

def _archived_student(db, user_factory, vk_id, curator):
    from datetime import datetime, timezone

    student = user_factory(vk_id=vk_id, name=f"Архивный {vk_id}")
    student.curator_id = curator.id
    student.archived_at = datetime.now(timezone.utc)
    student.is_active = False
    db.commit()
    return student


def test_curator_without_grant_has_no_archive(client, session_factory, curator):
    _login(client, session_factory, curator)
    assert client.get("/cabinet/archive", follow_redirects=False).status_code == 403
    assert "archive" not in [i.key for i in curator_nav_items()]


def test_granted_curator_sees_whole_school_archive(
    client, db, session_factory, user_factory, curator,
):
    """Владелец 30.09.2026: «Куратор 1» без своих учеников видел пустой архив —
    куратору с личным доступом открыт весь архив школы, как ГП."""
    other = user_factory(vk_id=990_530, name="Чужой куратор", role_name="куратор")
    mine = _archived_student(db, user_factory, 990_531, curator)
    foreign = _archived_student(db, user_factory, 990_532, other)
    _personal(db, curator, "archive", "view")
    _login(client, session_factory, curator)

    page = client.get("/cabinet/archive", follow_redirects=False)
    assert page.status_code == 200
    assert mine.name in page.text
    assert foreign.name in page.text

    assert client.get(f"/cabinet/students/{mine.id}/profile").status_code == 200
    assert client.get(f"/cabinet/students/{foreign.id}/profile").status_code == 200


def test_granted_archive_does_not_open_foreign_active_students(
    client, db, session_factory, user_factory, curator,
):
    """Весь архив — да, чужие действующие ученики — нет."""
    other = user_factory(vk_id=990_535, name="Чужой куратор 2", role_name="куратор")
    active = user_factory(vk_id=990_536, name="Действующий чужой")
    active.curator_id = other.id
    db.commit()
    _personal(db, curator, "archive", "view")
    _login(client, session_factory, curator)

    assert client.get(f"/cabinet/students/{active.id}/profile").status_code == 403
    assert active.name not in client.get("/cabinet/students").text


def test_curator_without_grant_cannot_read_foreign_archived_card(
    client, db, session_factory, user_factory, curator,
):
    other = user_factory(vk_id=990_537, name="Чужой куратор 3", role_name="куратор")
    foreign = _archived_student(db, user_factory, 990_538, other)
    _login(client, session_factory, curator)
    assert client.get(f"/cabinet/students/{foreign.id}/profile").status_code == 404


def test_granted_archive_stays_read_only(client, db, session_factory, user_factory, curator):
    from app.models.work import Work

    student = _archived_student(db, user_factory, 990_540, curator)
    work = Work(
        user_id=student.id, work_type="mock_exam", month="Сентябрь", year=2026,
        filename="w.jpg", status="success",
    )
    db.add(work)
    db.commit()
    _personal(db, curator, "archive", "view")
    _login(client, session_factory, curator)
    resp = client.post(
        f"/cabinet/students/{student.id}/works/{work.id}/score",
        data={"score": "80"}, follow_redirects=False,
    )
    # С 30.09.2026 балл куратору закрыт рангом (`require_scorer`) раньше, чем
    # до ученика доходит проверка архива, — отсюда 403, а не прежний 404.
    # Запись в архив под ГП стерегут тесты `test_archived_student_writes.py`.
    assert resp.status_code == 403
    db.refresh(work)
    assert work.score is None


def test_granted_archive_appears_in_curator_menu():
    keys = [i.key for i in curator_nav_items(granted_sections=frozenset({"archive"}))]
    assert keys.index("archive") == keys.index("notifications") - 1


def test_curator_menu_shows_archive_when_granted(client, db, session_factory, curator):
    _personal(db, curator, "archive", "view")
    _login(client, session_factory, curator)
    resp = client.get("/cabinet/curator", follow_redirects=False)
    assert 'href="/cabinet/archive"' in resp.text


def test_card_shows_every_section_and_marks_above_role(db, superadmin, curator):
    rules = {r["key"]: r for r in section_access.user_rules(db, curator)}
    assert set(rules) == set(section_access.SECTIONS_BY_KEY) | set(section_access.ACTIONS_BY_KEY)
    assert rules["archive"]["native"] is False
    assert rules["archive"]["state"] == "role"
    assert rules["archive"]["default"] == "none"
    assert rules["archive"]["levels"] == ("none", "view")
    assert rules["students"]["native"] is True

    section_access.save_user_rules(
        db, actor_id=superadmin.id, target=curator, desired={"archive": "view"},
    )
    assert granted_sections(db, curator) == frozenset({"archive"})
    section_access.save_user_rules(
        db, actor_id=superadmin.id, target=curator, desired={"archive": "role"},
    )
    assert db.query(SectionAccessRule).count() == 0


def test_personal_close_beats_role_opened_above_rank(db, superadmin, curator, user_factory):
    other = user_factory(vk_id=990_560, name="Другой куратор", role_name="куратор")
    _open_for_role(db, superadmin, "куратор", "program")
    _personal(db, curator, "program", "none")
    assert "program" not in granted_sections(db, curator)
    assert "program" in granted_sections(db, other)


# ── Открыть сверх роли (владелец 03.10.2026) ──────────────────────────────────

def test_curator_with_program_works_in_it_as_head(client, db, session_factory, superadmin, curator):
    _personal(db, curator, "program", "edit")
    _login(client, session_factory, curator)
    page = client.get("/cabinet/staff/program/cycles", follow_redirects=False)
    assert page.status_code == 200
    # Меню — своё, кураторское, с пунктом открытого раздела, без меню ГП.
    assert 'aria-label="Меню куратора"' in page.text
    assert 'href="/cabinet/staff/program/cycles"' in page.text
    assert 'href="/cabinet/staff/point-a"' not in page.text
    # Соседние разделы ГП остаются закрыты рангом.
    assert client.get("/cabinet/staff/point-a", follow_redirects=False).status_code == 403
    assert client.get("/cabinet/superadmin/users", follow_redirects=False).status_code == 403


def test_role_wide_open_reaches_every_curator(client, db, session_factory, superadmin, curator, user_factory):
    other = user_factory(vk_id=990_561, name="Второй куратор", role_name="куратор")
    _open_for_role(db, superadmin, "куратор", "point_a")
    for who in (curator, other):
        _login(client, session_factory, who)
        assert client.get("/cabinet/staff/point-a", follow_redirects=False).status_code == 200


def test_open_section_does_not_lift_rank_for_score(client, db, session_factory, curator, user_factory):
    """Балл ставит только ГП (30.09.2026) — и в разделе, открытом сверх роли."""
    student = user_factory(vk_id=990_562, name="Ученик точки А")
    _personal(db, curator, "point_a", "edit")
    _login(client, session_factory, curator)
    assert client.get(f"/cabinet/staff/point-a/{student.id}", follow_redirects=False).status_code != 403
    resp = client.post(
        f"/cabinet/staff/point-a/{student.id}/portfolio-after/score",
        json={"score": 50}, follow_redirects=False,
    )
    assert resp.status_code == 403


def test_open_mock_check_shows_any_students_mock_exams(
    client, db, session_factory, user_factory, curator,
):
    """«Проверка пробников» грузит пробники любого ученика школы — у куратора
    с открытым разделом этот адрес работает и для чужих учеников."""
    other = user_factory(vk_id=990_563, name="Чужой куратор 4", role_name="куратор")
    foreign = user_factory(vk_id=990_564, name="Чужой ученик")
    foreign.curator_id = other.id
    db.commit()
    _login(client, session_factory, curator)
    assert client.get(f"/cabinet/students/{foreign.id}/mock-exams").status_code in (403, 404)
    _personal(db, curator, "mock_check", "edit")
    assert client.get("/cabinet/admin/mock-check", follow_redirects=False).status_code == 200
    assert client.get(f"/cabinet/students/{foreign.id}/mock-exams").status_code == 200
    # Остальные вкладки чужой карточки по-прежнему закрыты.
    assert client.get(f"/cabinet/students/{foreign.id}/profile").status_code in (403, 404)


def test_moderator_open_section_is_read_only(client, db, session_factory, superadmin, moderator):
    _login(client, session_factory, moderator)
    assert client.get("/cabinet/staff/program/cycles", follow_redirects=False).status_code == 403
    _open_for_role(db, superadmin, "модератор", "program", level="view")
    assert client.get("/cabinet/staff/program/cycles", follow_redirects=False).status_code == 200
    resp = client.post(
        "/cabinet/staff/program/stages", data={"title": "Новый этап"}, follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "Модератору открыты" in resp.text
    keys = [i.key for i in staff_nav_items(4, "модератор", None, frozenset({"program", "people"}))]
    assert keys == ["students", "archive", "activity", "program", "people"]


def test_moderator_home_falls_back_to_granted_section(client, db, session_factory, superadmin, moderator):
    _close_for_role(db, superadmin, "модератор", "students", "archive", "statistics")
    _open_for_role(db, superadmin, "модератор", "program", level="view")
    _login(client, session_factory, moderator)
    resp = client.get("/cabinet", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/staff/program/cycles"


def test_curator_menu_lists_granted_sections_in_catalog_order():
    keys = [i.key for i in curator_nav_items(granted_sections=frozenset({"people", "program"}))]
    assert keys.index("program") < keys.index("people") < keys.index("notifications")
