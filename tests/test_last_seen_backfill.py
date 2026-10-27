"""Миграция `e3f9a1c7b2d4`: «был на платформе» до первого пинга — по
последнему настоящему действию (Александрия 27.10.2026: в «Давно не
заходили» стояло 11–12 октября у тех, кто заходил недавно)."""
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

_MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/e3f9a1c7b2d4_last_seen_backfill.py"

# Первый пинг — 24.10.2026 12:00 UTC.
FIRST_PING = "2026-10-24 12:00:00"


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, last_seen_at DATETIME)")
        conn.exec_driver_sql("CREATE TABLE user_activity_days (id INTEGER PRIMARY KEY, first_seen_at DATETIME)")
        conn.exec_driver_sql(
            "CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INTEGER, expires_at DATETIME,"
            " impersonated_by_id INTEGER)")
        conn.exec_driver_sql("CREATE TABLE video_view_logs (id INTEGER PRIMARY KEY, user_id INTEGER, opened_at DATETIME)")
        conn.exec_driver_sql(
            "CREATE TABLE task_block_submissions (id INTEGER PRIMARY KEY, user_id INTEGER, submitted_at DATETIME)")
    return engine


def _run(engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("last_seen_backfill", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _seen(engine):
    with engine.connect() as conn:
        return {row[0]: (str(row[1])[:16] if row[1] else None)
                for row in conn.exec_driver_sql("SELECT id, last_seen_at FROM users")}


def test_raises_stale_date_to_last_real_action_before_first_ping():
    engine = _engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(f"INSERT INTO user_activity_days (first_seen_at) VALUES ('{FIRST_PING}')")
        conn.exec_driver_sql(
            "INSERT INTO users VALUES (1, '2026-10-11 09:00:00'), (2, '2026-10-12 09:00:00'),"
            " (3, '2026-10-25 08:00:00'), (4, NULL), (5, '2026-10-12 09:00:00')")
        # 1: сессия продлена 20.10 в 18:00 — истекает 21.10 в 18:00.
        conn.exec_driver_sql("INSERT INTO sessions (user_id, expires_at) VALUES (1, '2026-10-21 18:00:00')")
        # 2: сдал работу 22.10, видео открыл 19.10.
        conn.exec_driver_sql("INSERT INTO task_block_submissions (user_id, submitted_at) VALUES (2, '2026-10-22 15:30:00')")
        conn.exec_driver_sql("INSERT INTO video_view_logs (user_id, opened_at) VALUES (2, '2026-10-19 10:00:00')")
        # 3: пинг уже поставил 25.10 — старое действие его не опускает.
        conn.exec_driver_sql("INSERT INTO video_view_logs (user_id, opened_at) VALUES (3, '2026-10-20 10:00:00')")
        # 4: не заходил с 11.07, но открывал видео 18.10.
        conn.exec_driver_sql("INSERT INTO video_view_logs (user_id, opened_at) VALUES (4, '2026-10-18 07:00:00')")
        # 5: сессия «глазами» и действия после первого пинга не считаются.
        conn.exec_driver_sql(
            "INSERT INTO sessions (user_id, expires_at, impersonated_by_id) VALUES (5, '2026-10-23 10:00:00', 3)")
        conn.exec_driver_sql("INSERT INTO task_block_submissions (user_id, submitted_at) VALUES (5, '2026-10-26 10:00:00')")

    _run(engine)

    assert _seen(engine) == {
        1: "2026-10-20 18:00",
        2: "2026-10-22 15:30",
        3: "2026-10-25 08:00",
        4: "2026-10-18 07:00",
        5: "2026-10-12 09:00",
    }


def test_without_pings_changes_nothing():
    engine = _engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users VALUES (1, '2026-10-11 09:00:00')")
        conn.exec_driver_sql("INSERT INTO video_view_logs (user_id, opened_at) VALUES (1, '2026-10-20 10:00:00')")

    _run(engine)

    assert _seen(engine) == {1: "2026-10-11 09:00"}
