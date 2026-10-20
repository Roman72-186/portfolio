"""Tests for GET/POST /login (staff login) and /auth/staff/login redirect."""
import bcrypt


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
# GET /auth/staff/login → redirect
# ---------------------------------------------------------------------------

def test_auth_staff_login_redirects_to_login(client):
    resp = client.get("/auth/staff/login", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# POST /login — successful login
# ---------------------------------------------------------------------------

def test_login_post_success_signs_in_and_leads_to_cabinet(client, db, user_factory):
    _make_staff_user(db, user_factory, password="secret42")
    resp = client.post("/login", data={"login": "testcurator", "password": "secret42"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "session_id" in resp.cookies
    assert "/cabinet" in resp.headers["location"]


def test_login_post_case_insensitive(client, db, user_factory):
    """Login should work regardless of case."""
    _make_staff_user(db, user_factory, staff_login="TestCurator", password="abc")
    resp = client.post("/login", data={"login": "testcurator", "password": "abc"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "session_id" in resp.cookies


# ---------------------------------------------------------------------------
# POST /login — failure cases
# ---------------------------------------------------------------------------

def test_login_post_wrong_password_is_refused(client, db, user_factory):
    _make_staff_user(db, user_factory, password="correct")
    resp = client.post("/login", data={"login": "testcurator", "password": "wrong"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert "session_id" not in resp.cookies


def test_login_post_unknown_user_is_refused(client):
    resp = client.post("/login", data={"login": "nobody", "password": "anything"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert "session_id" not in resp.cookies


def test_login_post_no_password_hash_is_refused(client, db, user_factory):
    user = user_factory(vk_id=555002, name="No Hash", role_name="куратор")
    user.staff_login = "nohash"
    user.password_hash = None
    db.add(user)
    db.commit()
    resp = client.post("/login", data={"login": "nohash", "password": "any"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert "session_id" not in resp.cookies


def test_login_post_inactive_user_blocked(client, db, user_factory):
    _make_staff_user(db, user_factory, is_active=False, password="pw")
    resp = client.post("/login", data={"login": "testcurator", "password": "pw"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert "session_id" not in resp.cookies


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


# ---------------------------------------------------------------------------
# Код-ревью 28.09.2026, P2 № 16: время ответа не выдаёт, есть ли логин
# ---------------------------------------------------------------------------
# Меряется не время (флаки), а то, что bcrypt работает на каждой ветке отказа:
# без этого несуществующий логин отвечал без checkpw — на сотни миллисекунд
# быстрее, и логины сотрудников перебирались по секундомеру.

from unittest.mock import patch


def _count_checkpw():
    return patch("bcrypt.checkpw", wraps=bcrypt.checkpw)


def test_unknown_login_still_runs_bcrypt(client):
    with _count_checkpw() as checkpw:
        resp = client.post("/login", data={"login": "nobody-here", "password": "x"})
    assert resp.status_code == 200
    assert "Неверный логин или пароль" in resp.text
    assert checkpw.call_count == 1


def test_login_without_password_hash_still_runs_bcrypt(client, db, user_factory):
    user = _make_staff_user(db, user_factory)
    user.password_hash = None
    db.commit()
    with _count_checkpw() as checkpw:
        resp = client.post("/login", data={"login": "testcurator", "password": "x"})
    assert "Неверный логин или пароль" in resp.text
    assert checkpw.call_count == 1
