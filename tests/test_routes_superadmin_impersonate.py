"""Тесты для имперсонации куратора суперадмином и админом.

Проверяет, что:
- POST /cabinet/superadmin/impersonate/{id} создаёт новую сессию для target
  и кладёт подписанную cookie с оригинальной сессией.
- POST /cabinet/superadmin/impersonate/stop работает без проверки роли
  (аварийный выход) — восстанавливает оригинальную сессию.
- /cabinet после восстановления отправляет суперадмина на /cabinet/superadmin,
  админа на /cabinet/admin-panel.
- Нельзя имперсонировать роль ≥ собственной.
"""
from itsdangerous import URLSafeTimedSerializer


def _csrf_for(client, session_id: str) -> str:
    from app.csrf import generate_csrf_token
    return generate_csrf_token(session_id)


def _impersonate_signer():
    from app.config import settings
    return URLSafeTimedSerializer(settings.session_secret, salt="impersonation-v1")


def test_superadmin_impersonates_curator_then_returns(
    client, session_factory, user_factory
):
    sa = user_factory(vk_id=900_001, name="SA One", role_name="суперадмин")
    curator = user_factory(vk_id=900_002, name="Curator Two", role_name="куратор")
    sa_sess = session_factory(sa)
    client.cookies.set("session_id", sa_sess.id)

    csrf = _csrf_for(client, sa_sess.id)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{curator.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet"
    # cookies were rewritten
    cookies = r.cookies
    assert "session_id" in cookies and cookies["session_id"] != sa_sess.id
    assert "impersonation_original" in cookies
    # signed cookie matches original
    payload = _impersonate_signer().loads(cookies["impersonation_original"])
    assert payload == sa_sess.id

    # Now stop — без CSRF, без require_*, без role check
    new_sess_id = cookies["session_id"]
    client.cookies.set("session_id", new_sess_id)
    client.cookies.set("impersonation_original", cookies["impersonation_original"])
    r2 = client.post("/cabinet/superadmin/impersonate/stop", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/cabinet"
    assert r2.cookies.get("session_id") == sa_sess.id

    # Follow /cabinet — superadmin lands on /cabinet/superadmin
    client.cookies.set("session_id", sa_sess.id)
    r3 = client.get("/cabinet", follow_redirects=False)
    assert r3.status_code in (301, 302, 303, 307, 308)
    assert r3.headers["location"] == "/cabinet/superadmin"


def test_admin_impersonates_curator_then_returns_to_admin_panel(
    client, session_factory, user_factory
):
    admin = user_factory(vk_id=900_010, name="Admin One", role_name="админ")
    curator = user_factory(vk_id=900_011, name="Curator Two", role_name="куратор")
    admin_sess = session_factory(admin)
    client.cookies.set("session_id", admin_sess.id)

    csrf = _csrf_for(client, admin_sess.id)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{curator.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    new_sess_id = r.cookies["session_id"]
    signed = r.cookies["impersonation_original"]

    # Stop
    client.cookies.set("session_id", new_sess_id)
    client.cookies.set("impersonation_original", signed)
    r2 = client.post("/cabinet/superadmin/impersonate/stop", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.cookies.get("session_id") == admin_sess.id

    # Admin lands on /cabinet/admin-panel
    client.cookies.set("session_id", admin_sess.id)
    r3 = client.get("/cabinet", follow_redirects=False)
    assert r3.headers["location"] == "/cabinet/admin-panel"


def test_admin_cannot_impersonate_superadmin(
    client, session_factory, user_factory
):
    admin = user_factory(vk_id=900_020, name="Admin Two", role_name="админ")
    sa = user_factory(vk_id=900_021, name="SA Three", role_name="суперадмин")
    sess = session_factory(admin)
    client.cookies.set("session_id", sess.id)
    csrf = _csrf_for(client, sess.id)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{sa.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_admin_cannot_impersonate_another_admin(
    client, session_factory, user_factory
):
    admin1 = user_factory(vk_id=900_030, name="Admin Four", role_name="админ")
    admin2 = user_factory(vk_id=900_031, name="Admin Five", role_name="админ")
    sess = session_factory(admin1)
    client.cookies.set("session_id", sess.id)
    csrf = _csrf_for(client, sess.id)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{admin2.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_curator_cannot_impersonate(
    client, session_factory, user_factory
):
    curator = user_factory(vk_id=900_040, name="Cur One", role_name="куратор")
    target = user_factory(vk_id=900_041, name="Stu One", role_name="ученик")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    csrf = _csrf_for(client, sess.id)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{target.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_impersonate_stop_without_cookie_is_safe(client):
    """Без impersonation_original cookie endpoint не падает — просто редиректит."""
    r = client.post("/cabinet/superadmin/impersonate/stop", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet"
