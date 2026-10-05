"""Миграция `c5f2a8d1e7b3`: период «Предобучение» над этапом «Предобучение».

Владелец 06.10.2026 выбрал завести структуру миграцией при выкатке. Тест
гоняет настоящие `upgrade()`/`downgrade()` на SQLite, подменив `op`
миграции, — тот же приём, что `test_migration_pii_downgrade.py`.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from app.db.database import Base

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "c5f2a8d1e7b3_program_period_preobuchenie.py"
)
OPENS = datetime(2026, 9, 1, tzinfo=timezone.utc)
ENDS = datetime(2026, 10, 4, 20, 59, tzinfo=timezone.utc)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_c5f2a8d1e7b3", MIGRATION)
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


def _run(conn, step):
    module = _load_migration()
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
