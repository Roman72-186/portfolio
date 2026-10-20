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
from datetime import datetime, timedelta, timezone

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


def test_admin_impersonates_moderator(client, session_factory, user_factory):
    """Решение владельца 29.09.2026: Главный преподаватель входит «глазами» к
    любому, кроме суперадмина. Модератор — наблюдатель: уровень ГП у него
    только для чтения (`effective_rank` = 4), поэтому общий guard управления
    (`can_manage_user_by_rank`) его не пускал. Вход к другому ГП остаётся
    закрытым — вопрос владельцу открыт (тест выше)."""
    admin = user_factory(vk_id=900_040, name="Admin Six", role_name="админ")
    moderator = user_factory(vk_id=900_041, name="Moder One", role_name="модератор")
    sess = session_factory(admin)
    client.cookies.set("session_id", sess.id)
    csrf = _csrf_for(client, sess.id)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{moderator.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    # Внутри — кабинет модератора, и выход обратно не упирается в его
    # закрытый белый список адресов (`rbac._MODERATOR_WRITE_EXACT`).
    client.cookies.set("session_id", r.cookies["session_id"])
    client.cookies.set("impersonation_original", r.cookies["impersonation_original"])
    assert client.get("/cabinet", follow_redirects=False).headers["location"] == "/cabinet/students"
    back = client.post("/cabinet/superadmin/impersonate/stop", follow_redirects=False)
    assert back.status_code == 303
    assert back.cookies.get("session_id") == sess.id


def test_superadmin_cannot_impersonate_inactive_user(
    client, session_factory, user_factory
):
    sa = user_factory(vk_id=900_032, name="SA Active", role_name="суперадмин")
    target = user_factory(
        vk_id=900_033,
        name="Inactive Curator",
        role_name="куратор",
        is_active=False,
    )
    sess = session_factory(sa)
    client.cookies.set("session_id", sess.id)
    csrf = _csrf_for(client, sess.id)

    r = client.post(
        f"/cabinet/superadmin/impersonate/{target.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert r.status_code == 404


def test_already_impersonating_session_cannot_start_nested_impersonation(
    client, session_factory, user_factory
):
    sa = user_factory(vk_id=900_034, name="SA Original", role_name="суперадмин")
    admin = user_factory(vk_id=900_035, name="Admin First", role_name="админ")
    student = user_factory(vk_id=900_036, name="Student Second", role_name="ученик")
    sa_sess = session_factory(sa)
    client.cookies.set("session_id", sa_sess.id)

    csrf = _csrf_for(client, sa_sess.id)
    first = client.post(
        f"/cabinet/superadmin/impersonate/{admin.id}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert first.status_code == 303

    impersonation_session_id = first.cookies["session_id"]
    client.cookies.set("session_id", impersonation_session_id)
    nested_csrf = _csrf_for(client, impersonation_session_id)
    nested = client.post(
        f"/cabinet/superadmin/impersonate/{student.id}",
        data={"csrf_token": nested_csrf},
        follow_redirects=False,
    )

    assert nested.status_code == 400
    assert "Уже в режиме имперсонации" in nested.text


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


def _enter_and_leave(client, session_factory, user_factory, return_to=None, vk=900_100):
    sa = user_factory(vk_id=vk, name="SA Return", role_name="суперадмин")
    student = user_factory(vk_id=vk + 1, name="Ученик", role_name="ученик")
    sa_sess = session_factory(sa)
    client.cookies.set("session_id", sa_sess.id)
    data = {"csrf_token": _csrf_for(client, sa_sess.id)}
    if return_to is not None:
        data["return_to"] = return_to
    r = client.post(f"/cabinet/superadmin/impersonate/{student.id}", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    client.cookies.set("session_id", r.cookies["session_id"])
    client.cookies.set("impersonation_original", r.cookies["impersonation_original"])
    if "impersonation_return" in r.cookies:
        client.cookies.set("impersonation_return", r.cookies["impersonation_return"])
    r2 = client.post("/cabinet/superadmin/impersonate/stop", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.cookies.get("session_id") == sa_sess.id
    return student, r2.headers["location"]


def test_leave_returns_to_student_card(client, session_factory, user_factory):
    """Проход 06.10.2026, пункт 7: «Выйти обратно» из кабинета ученика вела на
    главную суперадмина, а не в карточку ученика, откуда вошли."""
    student, location = _enter_and_leave(client, session_factory, user_factory, return_to="/cabinet/students?student=7")

    assert location == "/cabinet/students?student=7"


def test_leave_without_return_goes_home(client, session_factory, user_factory):
    _, location = _enter_and_leave(client, session_factory, user_factory, vk=900_110)

    assert location == "/cabinet"


def test_leave_never_goes_to_another_site(client, session_factory, user_factory):
    for i, bad in enumerate(("https://evil.example/", "//evil.example/cabinet", "/cabinet\\..\\\\evil.example","/cabinet\r\nX: y")):
        client.cookies.clear()
        _, location = _enter_and_leave(client, session_factory, user_factory, return_to=bad, vk=900_120 + i * 2)
        assert location == "/cabinet", bad


def _expires(db, session_id):
    from app.models.session import Session
    sess = db.query(Session).filter(Session.id == session_id).one()
    db.refresh(sess)
    value = sess.expires_at
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def test_impersonation_session_is_not_extended(client, db, session_factory, user_factory):
    """Код-ревью 12.10.2026, P2 № 3: скользящее продление считало половину от
    суточного срока, и первый же запрос растягивал 30 минут «глазами» до суток.
    Ни страница кабинета, ни `GET /csrf` срок не двигают и куку не переставляют."""
    sa = user_factory(vk_id=900_200, name="SA Ttl", role_name="суперадмин")
    student = user_factory(vk_id=900_201, name="Student Ttl", role_name="ученик")
    sa_sess = session_factory(sa)
    client.cookies.set("session_id", sa_sess.id)
    started = datetime.now(timezone.utc)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{student.id}",
        data={"csrf_token": _csrf_for(client, sa_sess.id)},
        follow_redirects=False,
    )
    imp_id = r.cookies["session_id"]
    deadline = _expires(db, imp_id)
    assert deadline <= started + timedelta(minutes=31)

    client.cookies.clear()
    client.cookies.set("session_id", imp_id)
    for path in ("/csrf", "/cabinet/personal"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code < 400, (path, resp.status_code)
        assert "session_id" not in resp.cookies, path
        assert _expires(db, imp_id) == deadline, path


def test_impersonation_session_ends_after_its_deadline(client, db, session_factory, user_factory):
    from app.models.session import Session
    sa = user_factory(vk_id=900_210, name="SA End", role_name="суперадмин")
    student = user_factory(vk_id=900_211, name="Student End", role_name="ученик")
    sa_sess = session_factory(sa)
    client.cookies.set("session_id", sa_sess.id)
    r = client.post(
        f"/cabinet/superadmin/impersonate/{student.id}",
        data={"csrf_token": _csrf_for(client, sa_sess.id)},
        follow_redirects=False,
    )
    imp_id = r.cookies["session_id"]
    # 31 минута спустя — срок ушёл, и тот же запрос получает 401.
    db.query(Session).filter(Session.id == imp_id).update(
        {Session.expires_at: datetime.now(timezone.utc) - timedelta(minutes=1)}
    )
    db.commit()
    client.cookies.clear()
    client.cookies.set("session_id", imp_id)
    resp = client.get("/cabinet/personal", headers={"accept": "application/json"}, follow_redirects=False)
    assert resp.status_code == 401


def test_ordinary_session_still_slides(client, db, session_factory, user_factory):
    student = user_factory(vk_id=900_220, name="Student Slide", role_name="ученик")
    sess = session_factory(student, hours=1)
    before = _expires(db, sess.id)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/csrf")
    assert resp.status_code == 200
    assert resp.cookies.get("session_id") == sess.id
    assert _expires(db, sess.id) > before + timedelta(hours=1)
