"""Уровни доступа в разделах: «Нет / Смотреть / Менять» (владелец 04.10.2026).

Шаг 1 плана `plans/2026-10-04-apparchi-тонкие-доступы.md`: галочка «открыт /
закрыт» стала уровнем, особая ветка модератора ушла в общее правило. Права
на проде при этом не меняются — это сторожат тесты миграции ниже.
"""
import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.models.section_access import SectionAccessRule
from app.services import section_access
from app.services.section_access import (
    SECTION_CLOSED_DETAIL,
    SECTION_VIEW_ONLY_DETAIL,
    SECTIONS,
    can,
    judge_request,
    resolve_levels,
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


@pytest.mark.parametrize("section", ["archive", "statistics", "mock_check", "lab3d"])
def test_migration_preserves_historical_read_only_sections(section):
    engine = _old_table([(section, 2, None, True)])
    _run(engine, "upgrade")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT level FROM section_access_rules").scalar_one() == "view"


# ── До и после миграции — одни и те же права ─────────────────────────────────
#

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


def test_prod_rows_keep_every_right_after_migration(db, user_factory, client, session_factory):
    """Проверяем реальные запросы с перенесёнными ролевыми и личными правами."""
    users = {
        "curator_169": user_factory(vk_id=991_001, name="Куратор 169", role_name="куратор"),
        "moderator_278": user_factory(vk_id=991_002, name="Модератор 278", role_name="модератор"),
        "moderator": user_factory(vk_id=991_003, name="Модератор", role_name="модератор"),
        "curator": user_factory(vk_id=991_004, name="Куратор", role_name="куратор"),
        "head": user_factory(vk_id=991_005, name="ГП", role_name="админ"),
    }
    role_ids = {u.role.name: u.role_id for u in users.values()}
    _store_migrated(db, role_ids=role_ids, users=users)
    for role, expected in [("moderator", 200), ("moderator_278", 200), ("curator", 403), ("head", 200)]:
        _login(client, session_factory, users[role])
        assert client.get("/cabinet/staff/program/cycles", follow_redirects=False).status_code == expected
    _login(client, session_factory, users["curator_169"])
    assert client.get("/cabinet/archive", follow_redirects=False).status_code == 200

    from test_routes_program_cycles import _payload
    for role, expected in [("moderator", 403), ("moderator_278", 200)]:
        _login(client, session_factory, users[role])
        assert client.post("/cabinet/staff/program/cycles", json=_payload()).status_code == expected


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
        # Рассылки ГП открывают явным правилом в «Доступах».
        "админ": {s.key: "view" if s.key in view_only else "edit"
                  for s in SECTIONS if s.key != "broadcasts"},
    }
    for i, (role_name, native) in enumerate(expected.items()):
        user = user_factory(vk_id=991_100 + i, name=role_name, role_name=role_name)
        levels = resolve_levels(db, user_id=user.id, role_id=user.role_id, role_name=role_name)
        sections = {k: v for k, v in levels.items() if k in section_access.SECTIONS_BY_KEY}
        assert sections == {s.key: native.get(s.key, "none") for s in SECTIONS}, role_name


# Матрица: «Люди» целиком у ГП — пять обычных действий; бывшие «только СА»
# ни у кого. Куратору и модератору — ничего.
TODAY_ACTIONS = {
    "куратор": set(),
    "модератор": set(),
    "админ": {"people:students", "people:block", "people:role", "people:login", "people:impersonate",
              "statistics:bot_links"},
}


@pytest.mark.parametrize("role_name", list(TODAY_ACTIONS))
def test_action_defaults_repeat_todays_rights(db, user_factory, role_name):
    """Без строк в базе действие открыто ровно тем, кому сегодня открыт его
    адрес, и на каждом адресе действия слой решает так же."""
    user = user_factory(vk_id=991_150 + len(role_name), name=role_name, role_name=role_name)
    levels = resolve_levels(db, user_id=user.id, role_id=user.role_id, role_name=role_name)
    opened = {a.key for a in section_access.ACTIONS if levels[a.key] == "edit"}
    assert opened == TODAY_ACTIONS[role_name]
    for key, pattern, methods in section_access._ACTION_RULES:
        # Пример адреса из шаблона: число вместо `\d+`, первая ветка из `(a|b)`.
        path = pattern.pattern.strip("^$").replace(r"\d+", "5")
        path = path.replace("[A-Za-z0-9_-]+", "test-code")
        path = re.sub(r"\(([^|)]*)\|[^)]*\)", r"\1", path).replace("\\", "")
        assert pattern.match(path), path
        method = sorted(methods)[0]
        access = judge_request(method, path, {}, levels, role_name)
        assert (access.refusal is None) is (key in TODAY_ACTIONS[role_name]), (role_name, key, path)
        # Родное действие ранг не поднимает.
        assert access.rank == 0, (role_name, key)


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


def test_migration_keeps_explicit_denial_for_personal_overrides():
    engine = _old_table([("program", None, 278, False)])
    _run(engine, "upgrade")
    with engine.connect() as conn:
        assert tuple(conn.exec_driver_sql("SELECT user_id, level FROM section_access_rules").one()) == (278, "none")
