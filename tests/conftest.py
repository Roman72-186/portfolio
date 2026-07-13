"""
Shared test fixtures.

Patches app.db.database with an in-memory SQLite engine BEFORE importing
app.main so that the lifespan create_all and all route handlers use the
same test database.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

# ─── CWD must be portfolio-saas/ so relative paths (app/templates, app/static)
#     resolve correctly when FastAPI mounts them at import time.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_BASE)
sys.path.insert(0, _BASE)

# ─── Set DATABASE_URL before any app imports so pydantic-settings picks it up.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest                                         # noqa: E402
from fastapi.testclient import TestClient             # noqa: E402
from sqlalchemy import create_engine, StaticPool, event  # noqa: E402
from sqlalchemy.orm import sessionmaker              # noqa: E402

# ─── Patch the database module BEFORE importing app.main.
import app.db.database as _db_module                # noqa: E402

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(
    bind=_TEST_ENGINE, autoflush=False, expire_on_commit=False
)
_db_module.engine = _TEST_ENGINE
_db_module.SessionLocal = _TestSessionLocal

from app.db.database import Base, get_db             # noqa: E402
from app.main import app                             # noqa: E402
from app.models.session import Session as DbSession  # noqa: E402
from app.models.user import User                     # noqa: E402
from app.models.role import Role                     # noqa: E402
from app.dependencies import require_csrf            # noqa: E402
from app.config import settings as _app_settings     # noqa: E402


@pytest.fixture
def enable_n8n(monkeypatch):
    """Opt in only tests that explicitly exercise the legacy n8n contract."""
    monkeypatch.setattr(_app_settings, "n8n_enabled", True)

# ─── Fix SQLite naive-datetime issue.
#
#     SQLAlchemy's SQLite dialect strips timezone info when storing
#     DateTime(timezone=True), so values come back as naive datetimes.
#     The production code compares them with datetime.now(timezone.utc) which
#     raises: TypeError: can't compare offset-naive and offset-aware datetimes.
#
#     Solution: register SQLAlchemy ORM events that re-attach UTC timezone to
#     any naive DateTime(timezone=True) column immediately after DB load/refresh.

def _attach_utc(instance):
    for attr in instance.__mapper__.column_attrs:
        for col in attr.columns:
            if getattr(col.type, "timezone", False):
                val = getattr(instance, attr.key, None)
                if isinstance(val, datetime) and val.tzinfo is None:
                    setattr(instance, attr.key, val.replace(tzinfo=timezone.utc))


@event.listens_for(Base, "load", propagate=True)
def _on_load(instance, context):
    _attach_utc(instance)


@event.listens_for(Base, "refresh", propagate=True)
def _on_refresh(instance, context, attrs):
    _attach_utc(instance)


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_schema():
    """Create tables before each test, drop them after."""
    Base.metadata.create_all(bind=_TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=_TEST_ENGINE)


@pytest.fixture()
def db(_reset_schema):
    """Provide a SQLAlchemy session for direct DB manipulation in tests."""
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Feature period cache cleanup
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_feature_period_cache():
    """Clear the in-memory feature period cache before each test."""
    from app.services.feature_periods import invalidate_feature_cache
    invalidate_feature_cache()
    yield
    invalidate_feature_cache()


@pytest.fixture(autouse=True)
def mock_exam_access_default_time(monkeypatch):
    """Заморозить «сейчас» mock-exam доступа на сегодня 13:00 МСК.

    Доступ к пробнику гейтится по периоду opens_at..closes_at (точные дата+время),
    а билеты в фикстурах строятся относительно реального «сегодня»
    (start_date=today-1..end_date=today+30). Поэтому замораживаем now_msk на
    сегодняшнюю дату в середине дня — иначе now (фикс. дата из прошлого) выпал бы
    из периода доступа билета. Тест может переопределить это своим monkeypatch.
    """
    from app.services import mock_exam_access
    from app.services.tz import now_msk

    frozen = now_msk().replace(hour=13, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mock_exam_access, "now_msk", lambda: frozen)


# ---------------------------------------------------------------------------
# HTTP client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(db):
    """TestClient that uses the test DB session via dependency override."""
    def _override_get_db():
        yield db

    def _csrf_noop():
        pass

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_csrf] = _csrf_noop
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper factory fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def role_factory(db):
    """Return a callable that creates Role rows (or returns existing)."""
    def _make(name: str, rank: int, display_name: str | None = None) -> Role:
        existing = db.query(Role).filter(Role.name == name).first()
        if existing:
            return existing
        role = Role(name=name, rank=rank, display_name=display_name or name.capitalize())
        db.add(role)
        db.commit()
        db.refresh(role)
        return role
    return _make


@pytest.fixture()
def user_factory(db, role_factory):
    """Return a callable that creates User rows with sane defaults."""
    def _make(
        *,
        vk_id: int = 100_001,
        name: str = "Test Student",
        tariff: str = "УВЕРЕННЫЙ",
        is_admin: bool = False,
        is_active: bool = True,
        is_group_member: bool = True,
        profile_completed: bool = True,
        portfolio_do_completed: bool = True,
        course_periods: str | None = "10-14 июня",
        lessons_count: str | None = "8",
        role_name: str | None = "ученик",
    ) -> User:
        role_id = None
        if role_name:
            # rank mapping for test roles
            rank_map = {
                "ученик": 1, "куратор": 2, "модератор": 3,
                "админ": 4, "суперадмин": 5,
            }
            rank = rank_map.get(role_name, 1)
            role = role_factory(role_name, rank)
            role_id = role.id
        user = User(
            vk_id=vk_id,
            name=name,
            tariff=tariff,
            is_admin=is_admin,
            is_active=is_active,
            is_group_member=is_group_member,
            profile_completed=profile_completed,
            portfolio_do_completed=portfolio_do_completed,
            course_periods=course_periods if profile_completed else None,
            lessons_count=lessons_count if profile_completed else None,
            role_id=role_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture()
def session_factory(db):
    """Return a callable that creates Session rows."""
    def _make(user: User, *, hours: int = 24, active: bool = True) -> DbSession:
        sess = DbSession(
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
            is_active=active,
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)
        return sess

    return _make


@pytest.fixture()
def regular_user(user_factory):
    return user_factory()


@pytest.fixture()
def admin_user(user_factory):
    return user_factory(vk_id=999_999, name="Admin User", is_admin=True, role_name="суперадмин")


@pytest.fixture()
def auth_client(client, session_factory, regular_user):
    """TestClient with a valid session cookie for a regular student."""
    sess = session_factory(regular_user)
    client.cookies.set("session_id", sess.id)
    return client, regular_user


@pytest.fixture()
def admin_client(client, session_factory, admin_user):
    """TestClient with a valid session cookie for an admin user."""
    sess = session_factory(admin_user)
    client.cookies.set("session_id", sess.id)
    return client, admin_user
