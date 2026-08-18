"""Tests for GET /cabinet/personal — «Личная информация» (трек A)."""


def test_personal_without_auth_redirects(client):
    resp = client.get("/cabinet/personal", follow_redirects=False)
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_personal_redirects_to_profile_when_incomplete(client, user_factory, session_factory):
    user = user_factory(profile_completed=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/personal", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


def test_personal_shows_own_contacts(auth_client, db):
    client, user = auth_client
    user.phone = "+79991234567"
    user.tg_username = "self_view"
    db.add(user)
    db.commit()

    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert "+79991234567" in resp.text
    assert "@self_view" in resp.text


def test_personal_shows_placeholder_when_contact_missing(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert "Не указано" in resp.text


def test_personal_bottom_nav_highlights_personal_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert 'href="/cabinet/personal"' in resp.text
    assert 'class="bottom-nav"' in resp.text
