"""Миграции `c5f2a8d1e7b3` и `d8a3b6e2f1c4`: период «Предобучение» над этапом;
`e4b7c1d9a2f6`: период «1 семестр 2026-2027» над двумя этапами без периода.

Первая искала этап ровно «Предобучение», на проде он «Предобучение
2026-2027» — прошла впустую; вторая ищет по началу названия и берёт название
этапа целиком (владелец: «взять название из АОП»).

Владелец 06.10.2026 выбрал завести структуру миграцией при выкатке. Тест
гоняет настоящие `upgrade()`/`downgrade()` на SQLite, подменив `op`
миграции, — тот же приём, что `test_migration_pii_downgrade.py`.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from app.db.database import Base

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
MIGRATION = VERSIONS / "c5f2a8d1e7b3_program_period_preobuchenie.py"
FIX = VERSIONS / "d8a3b6e2f1c4_program_period_preobuchenie_title.py"
OPENS = datetime(2026, 9, 1, tzinfo=timezone.utc)
ENDS = datetime(2026, 10, 4, 20, 59, tzinfo=timezone.utc)


def _load_migration(path=MIGRATION):
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem[:12]}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeOp:
    def __init__(self, conn):
        self._conn = conn

    def get_bind(self):
        return self._conn


def _engine():
    engine = sa.create_engine("sqlite://")
    import app.models  # noqa: F401 — регистрирует все таблицы в Base.metadata
    Base.metadata.create_all(engine)
    return engine


def _topic(conn, *, title, kind, parent_id=None, deleted=False):
    conn.execute(
        sa.text(
            "INSERT INTO learning_topics (title, kind, parent_id, description, opens_at,"
            " ends_at, sort_order, assign_to_all, tariff_restricted, is_published,"
            " locks_next, deleted_at, created_at, updated_at)"
            " VALUES (:title, :kind, :parent_id, 'Описание', :opens, :ends, 0, 1, 0, 1,"
            " 1, :deleted, :opens, :opens)"
        ),
        {"title": title, "kind": kind, "parent_id": parent_id, "opens": OPENS,
         "ends": ENDS, "deleted": OPENS if deleted else None},
    )
    return conn.execute(sa.text("SELECT max(id) FROM learning_topics")).scalar()


def _rows(conn):
    return {
        row.id: row for row in conn.execute(
            sa.text("SELECT id, title, kind, parent_id, description, is_published FROM learning_topics")
        )
    }


def _run(conn, step, path=MIGRATION):
    module = _load_migration(path)
    module.op = _FakeOp(conn)
    getattr(module, step)()


def test_upgrade_puts_preobuchenie_stage_into_period():
    with _engine().begin() as conn:
        stage = _topic(conn, title="Предобучение", kind="stage")
        cycle = _topic(conn, title="", kind="week", parent_id=stage)
        semester = _topic(conn, title="Октябрь", kind="stage")

        _run(conn, "upgrade")
        rows = _rows(conn)

        periods = [r for r in rows.values() if r.kind == "period"]
        assert len(periods) == 1
        period = periods[0]
        assert period.title == "Предобучение"
        assert period.description == "Описание"
        assert period.parent_id is None
        assert bool(period.is_published)
        assert rows[stage].parent_id == period.id
        # Цикл остаётся в своём этапе, другие этапы без периода.
        assert rows[cycle].parent_id == stage
        assert rows[semester].parent_id is None


def test_upgrade_twice_does_not_duplicate():
    with _engine().begin() as conn:
        stage = _topic(conn, title="Предобучение", kind="stage")
        _run(conn, "upgrade")
        _run(conn, "upgrade")
        rows = _rows(conn)
        periods = [r for r in rows.values() if r.kind == "period"]
        assert len(periods) == 1
        assert rows[stage].parent_id == periods[0].id


def test_upgrade_without_stage_does_nothing():
    with _engine().begin() as conn:
        _topic(conn, title="Предобучение", kind="stage", deleted=True)
        _topic(conn, title="Октябрь", kind="stage")
        _run(conn, "upgrade")
        assert all(r.kind != "period" for r in _rows(conn).values())


def test_upgrade_keeps_stage_already_in_period():
    """Этап, который владелец уже вложил в период руками, миграция не трогает."""
    with _engine().begin() as conn:
        own = _topic(conn, title="Свой период", kind="period")
        stage = _topic(conn, title="Предобучение", kind="stage", parent_id=own)
        _run(conn, "upgrade")
        rows = _rows(conn)
        assert rows[stage].parent_id == own
        assert [r.title for r in rows.values() if r.kind == "period"] == ["Свой период"]


def test_downgrade_unlinks_and_removes_period():
    with _engine().begin() as conn:
        stage = _topic(conn, title="Предобучение", kind="stage")
        _run(conn, "upgrade")
        _run(conn, "downgrade")
        rows = _rows(conn)
        assert all(r.kind != "period" for r in rows.values())
        assert rows[stage].parent_id is None


def test_fix_takes_full_title_from_aop_like_on_prod():
    """Прод 06.10.2026: этап «Предобучение 2026-2027». Первая миграция его не
    видит, вторая заводит период с названием этапа целиком."""
    with _engine().begin() as conn:
        stage = _topic(conn, title="Предобучение 2026-2027", kind="stage")
        semester = _topic(conn, title="1 семестр_годовой курс 2026-2027", kind="stage")
        _run(conn, "upgrade")
        assert all(r.kind != "period" for r in _rows(conn).values())

        _run(conn, "upgrade", FIX)
        _run(conn, "upgrade", FIX)
        rows = _rows(conn)
        periods = [r for r in rows.values() if r.kind == "period"]
        assert [p.title for p in periods] == ["Предобучение 2026-2027"]
        assert rows[stage].parent_id == periods[0].id
        assert rows[semester].parent_id is None


def test_fix_skips_stage_already_linked_by_first_migration():
    with _engine().begin() as conn:
        stage = _topic(conn, title="Предобучение", kind="stage")
        _run(conn, "upgrade")
        _run(conn, "upgrade", FIX)
        rows = _rows(conn)
        assert len([r for r in rows.values() if r.kind == "period"]) == 1
        assert rows[stage].parent_id is not None


def test_fix_downgrade_unlinks_and_removes_period():
    with _engine().begin() as conn:
        stage = _topic(conn, title="Предобучение 2026-2027", kind="stage")
        _run(conn, "upgrade", FIX)
        _run(conn, "downgrade", FIX)
        rows = _rows(conn)
        assert all(r.kind != "period" for r in rows.values())
        assert rows[stage].parent_id is None


# ── «1 семестр 2026-2027» (e4b7c1d9a2f6) ────────────────────────────────────

SEMESTER = VERSIONS / "e4b7c1d9a2f6_program_period_first_semester.py"


def _dates(conn, topic_id):
    return conn.execute(
        sa.text("SELECT opens_at, ends_at FROM learning_topics WHERE id = :id"), {"id": topic_id}
    ).one()


def test_semester_puts_both_orphan_stages_into_one_period_like_on_prod():
    """Прод 06.10.2026: без периода остались «1 семестр_годовой курс
    2026-2027» (57) и «Октябрь» (60). Владелец: «к периоду привязывается этап»."""
    with _engine().begin() as conn:
        preob_period = _topic(conn, title="Предобучение 2026-2027", kind="period")
        preob = _topic(conn, title="Предобучение 2026-2027", kind="stage", parent_id=preob_period)
        year = _topic(conn, title="1 семестр_годовой курс 2026-2027", kind="stage")
        october = _topic(conn, title="Октябрь", kind="stage")
        cycle = _topic(conn, title="", kind="week", parent_id=year)

        _run(conn, "upgrade", SEMESTER)
        _run(conn, "upgrade", SEMESTER)
        rows = _rows(conn)

        semesters = [r for r in rows.values() if r.title == "1 семестр 2026-2027"]
        assert len(semesters) == 1
        semester = semesters[0]
        assert semester.kind == "period"
        assert semester.parent_id is None
        assert bool(semester.is_published)
        assert rows[year].parent_id == semester.id
        assert rows[october].parent_id == semester.id
        # Предобучение и цикл остаются где были.
        assert rows[preob].parent_id == preob_period
        assert rows[cycle].parent_id == year
        assert _dates(conn, semester.id) == _dates(conn, year)


def test_semester_reuses_period_made_by_hand():
    with _engine().begin() as conn:
        own = _topic(conn, title="1 семестр 2026-2027", kind="period")
        october = _topic(conn, title="Октябрь", kind="stage")
        _run(conn, "upgrade", SEMESTER)
        rows = _rows(conn)
        assert [r.id for r in rows.values() if r.kind == "period"] == [own]
        assert rows[october].parent_id == own


def test_semester_without_stages_does_nothing():
    with _engine().begin() as conn:
        _topic(conn, title="Октябрь", kind="stage", deleted=True)
        _topic(conn, title="Ноябрь", kind="stage")
        _run(conn, "upgrade", SEMESTER)
        assert all(r.kind != "period" for r in _rows(conn).values())


def test_semester_downgrade_unlinks_and_removes_period():
    with _engine().begin() as conn:
        october = _topic(conn, title="Октябрь", kind="stage")
        _run(conn, "upgrade", SEMESTER)
        _run(conn, "downgrade", SEMESTER)
        rows = _rows(conn)
        assert all(r.kind != "period" for r in rows.values())
        assert rows[october].parent_id is None
