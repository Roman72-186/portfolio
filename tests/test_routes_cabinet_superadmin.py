"""Tests for /cabinet/superadmin — dashboard, set-credentials, issue-link."""
import pytest

from app.models.login_token import LoginToken
from app.models.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def superadmin_client(client, db, user_factory, session_factory):
    user = user_factory(vk_id=900001, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


@pytest.fixture()
def curator_user(db, user_factory):
    user = user_factory(vk_id=900002, name="Curator User", role_name="куратор")
    user.first_name = "Иван"
    user.last_name = "Петров"
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# GET /cabinet/superadmin
# ---------------------------------------------------------------------------

def test_superadmin_dashboard_loads(superadmin_client):
    client, _ = superadmin_client
    resp = client.get("/cabinet/superadmin")
    assert resp.status_code == 200


def test_superadmin_dashboard_contains_stats(superadmin_client):
    client, _ = superadmin_client
    resp = client.get("/cabinet/superadmin")
    assert resp.status_code == 200
    # Should contain some dashboard content
    text = resp.text.lower()
    assert "пользовател" in text or "куратор" in text or "суперадмин" in text


def test_superadmin_dashboard_denied_for_curator(client, db, user_factory, session_factory):
    user = user_factory(vk_id=900010, name="Just Curator", role_name="куратор")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/superadmin", follow_redirects=False)
    assert resp.status_code == 403


def test_superadmin_dashboard_denied_for_student(client, db, user_factory, session_factory):
    user = user_factory(vk_id=900011, name="Student", role_name="ученик")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/superadmin", follow_redirects=False)
    assert resp.status_code == 403


def test_superadmin_dashboard_denied_no_session(client):
    resp = client.get("/cabinet/superadmin", follow_redirects=False)
    assert resp.status_code in (302, 401)


# ---------------------------------------------------------------------------
# POST /cabinet/superadmin/set-credentials
# ---------------------------------------------------------------------------

def test_set_credentials_generates_login(superadmin_client, db, curator_user):
    client, _ = superadmin_client
    resp = client.post("/cabinet/superadmin/set-credentials",
                       data={"target_user_id": curator_user.id, "csrf_token": "bypass"})
    assert resp.status_code == 200

    db.refresh(curator_user)
    assert curator_user.staff_login is not None
    assert curator_user.password_hash is not None


def test_set_credentials_shows_issued_creds(superadmin_client, db, curator_user):
    client, _ = superadmin_client
    resp = client.post("/cabinet/superadmin/set-credentials",
                       data={"target_user_id": curator_user.id, "csrf_token": "bypass"})
    assert resp.status_code == 200
    text = resp.text.lower()
    assert "логин" in text or "пароль" in text or "login" in text


def test_set_credentials_reuses_existing_login(superadmin_client, db, curator_user):
    """If staff_login already set, keep it; only reset password."""
    curator_user.staff_login = "existing.login"
    db.add(curator_user)
    db.commit()

    client, _ = superadmin_client
    client.post("/cabinet/superadmin/set-credentials",
                data={"target_user_id": curator_user.id, "csrf_token": "bypass"})

    db.refresh(curator_user)
    assert curator_user.staff_login == "existing.login"


def test_set_credentials_nonexistent_user_404(superadmin_client):
    client, _ = superadmin_client
    resp = client.post("/cabinet/superadmin/set-credentials",
                       data={"target_user_id": 99999, "csrf_token": "bypass"})
    assert resp.status_code == 404


def test_set_credentials_denied_for_curator(client, db, user_factory, session_factory, curator_user):
    curator = user_factory(vk_id=900020, name="Curator", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    resp = client.post("/cabinet/superadmin/set-credentials",
                       data={"target_user_id": curator_user.id, "csrf_token": "bypass"},
                       follow_redirects=False)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /cabinet/superadmin/issue-link
# ---------------------------------------------------------------------------

def test_issue_link_success(superadmin_client, db, curator_user):
    client, _ = superadmin_client
    resp = client.post("/cabinet/superadmin/issue-link",
                       data={"target_user_id": curator_user.id, "csrf_token": "bypass"})
    assert resp.status_code == 200

    # Token should be created in DB
    token = db.query(LoginToken).filter(LoginToken.user_id == curator_user.id).first()
    assert token is not None


def test_issue_link_shows_link_in_response(superadmin_client, db, curator_user):
    client, _ = superadmin_client
    resp = client.post("/cabinet/superadmin/issue-link",
                       data={"target_user_id": curator_user.id, "csrf_token": "bypass"})
    assert resp.status_code == 200
    assert "/auth/link?token=" in resp.text


def test_issue_link_nonexistent_user_404(superadmin_client):
    client, _ = superadmin_client
    resp = client.post("/cabinet/superadmin/issue-link",
                       data={"target_user_id": 99999, "csrf_token": "bypass"})
    assert resp.status_code == 404


def test_issue_link_denied_for_admin(client, db, user_factory, session_factory, curator_user):
    admin = user_factory(vk_id=900030, name="Admin", role_name="админ", is_admin=True)
    sess = session_factory(admin)
    client.cookies.set("session_id", sess.id)
    resp = client.post("/cabinet/superadmin/issue-link",
                       data={"target_user_id": curator_user.id, "csrf_token": "bypass"},
                       follow_redirects=False)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /cabinet/superadmin/users/assign-curator-bulk
# ---------------------------------------------------------------------------

def test_bulk_assign_curator_by_tg_usernames(superadmin_client, db, user_factory, curator_user):
    client, _ = superadmin_client
    student_1 = user_factory(vk_id=900101, name="Student One")
    student_2 = user_factory(vk_id=900102, name="Student Two")
    student_1.tg_username = "student_one"
    student_2.tg_username = "student_two"
    db.add_all([student_1, student_2])
    db.commit()

    resp = client.post(
        "/cabinet/superadmin/users/assign-curator-bulk",
        data={
            "curator_id": str(curator_user.id),
            "student_usernames": "@student_one\nstudent_two\nmissing_user",
            "cohort_tag": "may",
            "csrf_token": "bypass",
        },
    )

    assert resp.status_code == 200
    db.refresh(student_1)
    db.refresh(student_2)
    assert student_1.curator_id == curator_user.id
    assert student_2.curator_id == curator_user.id
    assert student_1.cohort_tag == "may"
    assert student_2.cohort_tag == "may"
    assert "missing_user" in resp.text


def test_bulk_assign_curator_requires_superadmin(client, db, user_factory, session_factory, curator_user):
    student = user_factory(vk_id=900103, name="Plain Student")
    sess = session_factory(student)
    client.cookies.set("session_id", sess.id)

    resp = client.post(
        "/cabinet/superadmin/users/assign-curator-bulk",
        data={
            "curator_id": str(curator_user.id),
            "student_usernames": "student_one",
            "csrf_token": "bypass",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 403
