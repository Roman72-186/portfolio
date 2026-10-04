"""Уровни доступа в разделах: «Нет / Смотреть / Менять» (владелец 04.10.2026).

Шаг 1 плана `plans/2026-10-04-apparchi-тонкие-доступы.md`: галочка «открыт /
закрыт» стала уровнем, особая ветка модератора ушла в общее правило. Права
на проде при этом не меняются — это сторожат тесты миграции ниже.
"""
import importlib.util
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.models.section_access import SectionAccessRule
from app.services import section_access
from app.services.rbac import is_moderator_request_allowed, is_score_request
from app.services.section_access import (
    SECTION_CLOSED_DETAIL,
    SECTION_VIEW_ONLY_DETAIL,
    SECTIONS,
    SECTIONS_BY_KEY,
    can,
    judge_request,
    resolve_levels,
    section_owners,
)

_MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/639c04979ebf_section_access_levels.py"

# Прод 04.10.2026, все строки `section_access_rules` (снято перед миграцией):
# (раздел, роль правила, владелец личного правила, is_open).
PROD_ROWS = (
    ("archive", None, "curator_169", True),
    ("program", "модератор", None, True),
    ("lab3d", "модератор", None, True),
    ("program", None, "moderator_278", True),
)


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


# ── Миграция на копии прод-строк ──────────────────────────────────────────────

def _load_migration():
    spec = importlib.util.spec_from_file_location("section_access_levels", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_table(rows):
    """Таблица в форме до миграции (`b8e4c2a6d0f3`) с данными `rows`.
    Роль модератора — id 14, как на проде."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE roles (id INTEGER PRIMARY KEY, name VARCHAR(50), rank INTEGER)")
        conn.exec_driver_sql(
            "INSERT INTO roles VALUES (2, 'куратор', 2), (14, 'модератор', 3), (4, 'админ', 4)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE section_access_rules ("
            " id INTEGER PRIMARY KEY, section_key VARCHAR(40) NOT NULL,"
            " role_id INTEGER, user_id INTEGER, is_open BOOLEAN NOT NULL DEFAULT 0,"
            " updated_by_id INTEGER, updated_at DATETIME)"
        )
        for key, role_id, user_id, is_open in rows:
            conn.exec_driver_sql(
                "INSERT INTO section_access_rules (section_key, role_id, user_id, is_open)"
                " VALUES (?, ?, ?, ?)",
                (key, role_id, user_id, is_open),
            )
    return engine


def _run(engine, step):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, step)()


def _columns(engine):
    with engine.connect() as conn:
        return [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(section_access_rules)")]


def test_migration_maps_prod_rows():
    engine = _old_table([
        ("archive", None, 169, True),
        ("program", 14, None, True),
        ("lab3d", 14, None, True),
        ("program", None, 278, True),
        # Закрытых строк на проде нет, но правило для них — «нет».
        ("reports", 2, None, False),
        # Строка роли не модератора сверх роли — «менять», как было.
        ("point_a", 2, None, True),
    ])
    _run(engine, "upgrade")
    assert "is_open" not in _columns(engine)
    with engine.connect() as conn:
        levels = conn.exec_driver_sql(
            "SELECT section_key, role_id, user_id, level FROM section_access_rules ORDER BY id"
        ).all()
    assert [tuple(row) for row in levels] == [
        ("archive", None, 169, "view"),
        ("program", 14, None, "view"),
        ("lab3d", 14, None, "view"),
        ("program", None, 278, "edit"),
        ("reports", 2, None, "none"),
        ("point_a", 2, None, "edit"),
    ]

    _run(engine, "downgrade")
    assert "level" not in _columns(engine)
    with engine.connect() as conn:
        flags = conn.exec_driver_sql("SELECT is_open FROM section_access_rules ORDER BY id").scalars().all()
    assert [bool(f) for f in flags] == [True, True, True, True, False, True]


def test_view_only_snapshot_matches_sections_without_writes():
    """Миграция переносит строки этих разделов в «Смотреть», потому что там
    нечего менять. Появится адрес на запись — снимок в миграции устарел бы
    молча; здесь краснеет."""
    from app.main import app

    writable = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = re.sub(r"\{[^}]*\}", "5", route.path)
        for method in route.methods - {"GET", "HEAD"}:
            for query in ({}, {"tab": "statistics"}):
                writable.update(section_owners(method, path, query))
    assert set(_load_migration().VIEW_ONLY_SECTIONS) == {s.key for s in SECTIONS} - writable


# ── До и после миграции — одни и те же права ─────────────────────────────────
#
# Прежняя логика `section_access.py` до 04.10.2026 (`de35e08`), сжатая до
# решения по запросу: белый список модератора, `moderator_may_use`,
# `blocked_section`, `elevated_rank`.

def _old_outcome(method, path, query, role_name, base_rank, rows):
    by_role = {key: is_open for key, scope, is_open in rows if scope == "role"}
    by_user = {key: is_open for key, scope, is_open in rows if scope == "user"}
    closed, granted = set(), set()
    for s in SECTIONS:
        native = role_name in s.roles
        is_open = by_user.get(s.key, by_role.get(s.key, native))
        if native and not is_open:
            closed.add(s.key)
        elif is_open and not native:
            granted.add(s.key)
    workable = {key for key in granted if by_user.get(key) is True}
    owners = section_owners(method, path, query)
    if role_name == "модератор":
        if method in ("GET", "HEAD"):
            may_use = any(key in granted for key in owners)
        else:
            may_use = any(key in workable for key in owners)
        if not (is_moderator_request_allowed(method, path) or may_use):
            return ("moderator", base_rank)
    if closed and owners and all(key in closed for key in owners):
        return ("closed", base_rank)
    rank = base_rank
    if granted and not is_score_request(method, path):
        for key in owners:
            if key in granted and SECTIONS_BY_KEY[key].elevates:
                rank = max(rank, SECTIONS_BY_KEY[key].min_rank)
    return ("ok", rank)


def _new_outcome(method, path, query, role_name, base_rank, levels):
    access = judge_request(method, path, query, levels, role_name)
    if role_name == "модератор" and not (
        is_moderator_request_allowed(method, path) or access.raised
    ):
        return ("moderator", base_rank)
    if access.refusal:
        return ("closed", base_rank)
    return ("ok", max(base_rank, access.rank))


def _requests():
    from app.main import app

    seen = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = re.sub(r"\{[^}]*\}", "5", route.path)
        for method in route.methods:
            for query in ({}, {"tab": "statistics"}):
                key = (method, path, tuple(query.items()))
                if key not in seen:
                    seen.add(key)
                    yield method, path, query


def _store_migrated(db, *, role_ids, users):
    """Прод-строки в тестовую базу — с уровнями, которые им дала миграция."""
    engine = _old_table([
        (key, 14 if role else None, users[owner].id if owner else None, is_open)
        for key, role, owner, is_open in PROD_ROWS
    ])
    _run(engine, "upgrade")
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT section_key, role_id, user_id, level FROM section_access_rules"
        ).all()
    for key, role_id, user_id, level in rows:
        db.add(SectionAccessRule(
            section_key=key, level=level, user_id=user_id,
            role_id=role_ids["модератор"] if role_id == 14 else None,
        ))
    db.commit()


def test_prod_rows_keep_every_right_after_migration(db, user_factory):
    """Каждый адрес приложения × каждый сотрудник с прод-правилами и без них:
    отказ, белый список и подъём ранга совпадают до и после."""
    users = {
        "curator_169": user_factory(vk_id=991_001, name="Куратор 169", role_name="куратор"),
        "moderator_278": user_factory(vk_id=991_002, name="Модератор 278", role_name="модератор"),
        "moderator": user_factory(vk_id=991_003, name="Модератор", role_name="модератор"),
        "curator": user_factory(vk_id=991_004, name="Куратор", role_name="куратор"),
        "head": user_factory(vk_id=991_005, name="ГП", role_name="админ"),
    }
    role_ids = {u.role.name: u.role_id for u in users.values()}
    _store_migrated(db, role_ids=role_ids, users=users)
    base_rank = {"куратор": 2, "модератор": 4, "админ": 4}

    mismatches = []
    checked = 0
    for label, user in users.items():
        role_name = user.role.name
        old_rows = [
            (key, "role", is_open) for key, role, owner, is_open in PROD_ROWS if role == role_name
        ] + [
            (key, "user", is_open) for key, role, owner, is_open in PROD_ROWS if owner == label
        ]
        levels = resolve_levels(db, user_id=user.id, role_id=user.role_id, role_name=role_name)
        for method, path, query in _requests():
            old = _old_outcome(method, path, query, role_name, base_rank[role_name], old_rows)
            new = _new_outcome(method, path, query, role_name, base_rank[role_name], levels)
            checked += 1
            if old != new:
                mismatches.append((label, method, path, query, old, new))
    assert checked > 1000
    assert mismatches == []


# ── Уровни по умолчанию — сегодняшние права ──────────────────────────────────

def test_default_levels_repeat_the_role_matrix(db, user_factory):
    """`reports/2026-10-04-матрица-прав-ролей.md`: без строк в базе уровень —
    то, что роль может сейчас."""
    # В разделах без адресов на запись (`VIEW_ONLY_SECTIONS`) положенное —
    # «Смотреть»: «Менять» там не к чему, и экран предлагает два уровня.
    view_only = section_access.VIEW_ONLY_SECTIONS
    expected = {
        "куратор": {"students": "edit", "students_review": "edit", "reports": "edit",
                    "statistics": "view", "lab3d": "view"},
        # Модератор — наблюдатель (28.09.2026).
        "модератор": {"students": "view", "archive": "view", "statistics": "view"},
        "админ": {s.key: "view" if s.key in view_only else "edit" for s in SECTIONS},
    }
    for i, (role_name, native) in enumerate(expected.items()):
        user = user_factory(vk_id=991_100 + i, name=role_name, role_name=role_name)
        levels = resolve_levels(db, user_id=user.id, role_id=user.role_id, role_name=role_name)
        assert levels == {s.key: native.get(s.key, "none") for s in SECTIONS}, role_name


# ── «Смотреть» на живых запросах ──────────────────────────────────────────────

def test_view_lifts_rank_only_for_reading(client, db, session_factory, user_factory):
    curator = user_factory(vk_id=991_200, name="Куратор АОП", role_name="куратор")
    db.add(SectionAccessRule(section_key="program", user_id=curator.id, level="view"))
    db.commit()
    _login(client, session_factory, curator)
    assert client.get("/cabinet/staff/program/cycles", follow_redirects=False).status_code == 200
    resp = client.post(
        "/cabinet/staff/program/stages", data={"title": "Новый этап"},
        headers={"Accept": "application/json"}, follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == SECTION_VIEW_ONLY_DETAIL


def test_head_lowered_to_view_reads_but_cannot_write(client, db, session_factory, user_factory):
    """Новое состояние: Главному преподавателю АОП можно поставить «Смотреть»."""
    head = user_factory(vk_id=991_201, name="ГП на просмотре", role_name="админ")
    db.add(SectionAccessRule(section_key="program", role_id=head.role_id, level="view"))
    db.commit()
    _login(client, session_factory, head)
    assert client.get("/cabinet/staff/program/cycles", follow_redirects=False).status_code == 200
    resp = client.post(
        "/cabinet/staff/program/stages", data={"title": "Новый этап"}, follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "Только просмотр" in resp.text
    assert SECTION_CLOSED_DETAIL not in resp.text


def test_score_is_never_lifted_by_any_level():
    for level in ("view", "edit"):
        access = judge_request(
            "POST", "/cabinet/staff/point-a/5/portfolio-after/score", {}, {"point_a": level}, "куратор",
        )
        assert access.rank == 0


# ── Кнопки спрашивают то же, что сервер ──────────────────────────────────────

@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ({"role_rank": 4, "nav_rank": 4, "section_levels": {"program": "edit"}}, True),
        ({"role_rank": 4, "nav_rank": 4, "section_levels": {"program": "view"}}, False),
        ({"role_rank": 4, "nav_rank": 2, "section_levels": {"program": "edit"}}, True),
        ({"role_rank": 5, "nav_rank": 5, "section_levels": {}}, True),
        ({"role_rank": 1, "nav_rank": 1, "section_levels": {}}, False),
    ],
)
def test_can_follows_levels(user, expected):
    assert can(user, "program") is expected


# ── Экран уровней (шаг 3): сохранение того же уровня ничего не меняет ─────────

def test_saving_same_levels_changes_nothing(db, user_factory):
    superadmin = user_factory(vk_id=991_300, name="СА", role_name="суперадмин")
    moderator = user_factory(vk_id=991_301, name="Модератор", role_name="модератор")
    curator = user_factory(vk_id=991_302, name="Куратор", role_name="куратор")
    section_access.save_role_levels(
        db, actor_id=superadmin.id, desired={"модератор": {"program": "view"}},
    )
    db.add(SectionAccessRule(section_key="archive", user_id=curator.id, level="view"))
    db.commit()
    assert section_access.role_levels(db)["модератор"]["program"] == "view"
    # Форма шлёт все ячейки — неизменённые не пишут ни строки, ни журнала.
    assert section_access.save_role_levels(
        db, actor_id=superadmin.id, desired={"модератор": {"program": "view", "students": "view"}},
    ) == 0
    assert section_access.save_user_rules(
        db, actor_id=superadmin.id, target=curator, desired={"archive": "view", "students": "role"},
    ) == 0
    # Личный уровень модератору — любой из трёх (владелец 04.10.2026).
    section_access.save_user_rules(
        db, actor_id=superadmin.id, target=moderator, desired={"people": "edit"},
    )
    assert db.query(SectionAccessRule).filter_by(user_id=moderator.id).one().level == "edit"
    # Вернуть «Как у роли» — строка уходит.
    section_access.save_user_rules(
        db, actor_id=superadmin.id, target=moderator, desired={"people": "role"},
    )
    assert db.query(SectionAccessRule).filter_by(user_id=moderator.id).count() == 0


def test_view_only_sections_match_migration_snapshot():
    """Экран предлагает два уровня ровно там, где миграция не нашла записи."""
    assert set(_load_migration().VIEW_ONLY_SECTIONS) == section_access.VIEW_ONLY_SECTIONS
