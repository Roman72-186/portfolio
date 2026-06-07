"""Tests for GET/POST /login (staff login) and /auth/staff/login redirect."""
import bcrypt
import pytest

from app.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _make_staff_user(db, user_factory, *, role_name="куратор", password="pass123",
                     is_active=True, staff_login="testcurator"):
    user = user_factory(vk_id=555001, name="Test Curator", role_name=role_name,
                        is_active=is_active)
    user.staff_login = staff_login
    user.password_hash = _hash(password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------

def test_login_get_returns_form(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "логин" in resp.text.lower() or "login" in resp.text.lower()
    assert "<form" in resp.text.lower()


def test_login_get_has_password_field(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert 'name="password"' in resp.text


# ---------------------------------------------------------------------------
# GET /auth/staff/login → redirect
# ---------------------------------------------------------------------------

def test_auth_staff_login_redirects_to_login(client):
    resp = client.get("/auth/staff/login", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# POST /login — successful login
# ---------------------------------------------------------------------------

def test_login_post_success_sets_cookie(client, db, user_factory):
    _make_staff_user(db, user_factory, password="secret42")
    resp = client.post("/login", data={"login": "testcurator", "password": "secret42"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "session_id" in resp.cookies


def test_login_post_success_redirects_to_cabinet(client, db, user_factory):
    _make_staff_user(db, user_factory, password="secret42")
    resp = client.post("/login", data={"login": "testcurator", "password": "secret42"},
                       follow_redirects=False)
    assert "/cabinet" in resp.headers["location"]


def test_login_post_case_insensitive(client, db, user_factory):
    """Login should work regardless of case."""
    _make_staff_user(db, user_factory, staff_login="TestCurator", password="abc")
    resp = client.post("/login", data={"login": "testcurator", "password": "abc"},
                       follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# POST /login — failure cases
# ---------------------------------------------------------------------------

def test_login_post_wrong_password_returns_200(client, db, user_factory):
    _make_staff_user(db, user_factory, password="correct")
    resp = client.post("/login", data={"login": "testcurator", "password": "wrong"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert "неверный" in resp.text.lower() or "пароль" in resp.text.lower()


def test_login_post_unknown_user_returns_200(client):
    resp = client.post("/login", data={"login": "nobody", "password": "anything"},
                       follow_redirects=False)
    assert resp.status_code == 200


def test_login_post_no_password_hash_returns_error(client, db, user_factory):
    user = user_factory(vk_id=555002, name="No Hash", role_name="куратор")
    user.staff_login = "nohash"
    user.password_hash = None
    db.add(user)
    db.commit()
    resp = client.post("/login", data={"login": "nohash", "password": "any"},
                       follow_redirects=False)
    assert resp.status_code == 200


def test_login_post_inactive_user_blocked(client, db, user_factory):
    _make_staff_user(db, user_factory, is_active=False, password="pw")
    resp = client.post("/login", data={"login": "testcurator", "password": "pw"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert "отключён" in resp.text.lower() or "заблокирован" in resp.text.lower()


def test_login_post_student_role_allowed(client, db, user_factory):
    """Students (rank 1) can use manual login/password access."""
    _make_staff_user(db, user_factory, role_name="ученик", password="pw")
    resp = client.post("/login", data={"login": "testcurator", "password": "pw"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "session_id" in resp.cookies


def test_login_post_very_long_input_no_crash(client):
    resp = client.post("/login",
                       data={"login": "x" * 1000, "password": "y" * 1000},
                       follow_redirects=False)
    assert resp.status_code in (200, 422)
