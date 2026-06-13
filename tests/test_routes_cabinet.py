"""Tests for /cabinet route."""


def test_cabinet_without_auth_redirects(client):
    resp = client.get("/cabinet", follow_redirects=False)
    # HTTPException(401) → redirected to /?error=session_expired
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_cabinet_with_valid_session_returns_200(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet")
    assert resp.status_code == 200


def test_cabinet_shows_student_name(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet")
    assert user.name in resp.text


def test_cabinet_shows_tariff(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet")
    assert user.tariff in resp.text


def test_cabinet_student_shows_mock_exam_empty_state(auth_client):
    """With no mock exams, cabinet/student shows empty-state CTA link."""
    client, _ = auth_client
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "пробник" in resp.text.lower()
    assert "/upload/mock-exam" in resp.text


def test_cabinet_blocked_user_gets_403(client, db, user_factory, session_factory):
    user = user_factory(is_active=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet", follow_redirects=False)
    # dependencies.py raises HTTP 403 for blocked users; there is no 403 handler so it passes through
    assert resp.status_code == 403
