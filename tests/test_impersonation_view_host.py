"""Вход «глазами» на отдельном адресе просмотра (план 2026-10-08).

Владелец 08.10.2026: ГП и суперадмин заходят в кабинет ученика или куратора и
не теряют свой. Просмотр живёт на `settings.view_origin`, у браузера там свой
пропуск; основной адрес при входе не получает ни одной куки.

Куки передаются заголовком, а банку клиента чистим перед каждым запросом:
httpx раскладывает куки по доменам, а здесь важно ровно то, что ушло.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

VIEW = "https://view.test"
MAIN = "https://main.test"


@pytest.fixture()
def view_mode(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "view_origin", VIEW)
    monkeypatch.setattr(settings, "main_origin", MAIN)


def _post(client, url, session_id=None, **kw):
    client.cookies.clear()
    headers = {"cookie": f"session_id={session_id}"} if session_id else {}
    return client.post(url, headers=headers, follow_redirects=False, **kw)


def _get(client, url, session_id=None, **kw):
    client.cookies.clear()
    headers = dict(kw.pop("headers", {}))
    if session_id:
        headers["cookie"] = f"session_id={session_id}"
    return client.get(url, headers=headers, follow_redirects=False, **kw)


def _start(client, actor_session, target_id, return_to=""):
    return _post(
        client, f"/cabinet/superadmin/impersonate/{target_id}", actor_session,
        data={"csrf_token": "x", "return_to": return_to},
    )


def _token(resp) -> str:
    return parse_qs(urlsplit(resp.headers["location"]).query)["t"][0]


def _people(user_factory, session_factory):
    sa = user_factory(vk_id=910_001, name="SA View", role_name="суперадмин")
    student = user_factory(vk_id=910_002, name="Student View", role_name="ученик")
    return sa, student, session_factory(sa)


def test_start_leaves_main_cabinet_untouched(client, db, view_mode, user_factory, session_factory):
    from app.models.session import Session
    sa, student, sa_sess = _people(user_factory, session_factory)

    r = _start(client, sa_sess.id, student.id)

    assert r.status_code == 303
    assert r.headers["location"].startswith(f"{VIEW}/cabinet/superadmin/impersonate/enter?t=")
    # Свой кабинет не трогаем: ни подмены пропуска, ни куки возврата.
    assert "set-cookie" not in r.headers
    imp = db.query(Session).filter(Session.user_id == student.id).one()
    assert imp.impersonated_by_id == sa.id
    assert imp.is_active is False  # включит только вход на адресе просмотра


def test_enter_gives_view_tab_its_own_pass_once(client, db, view_mode, user_factory, session_factory):
    from app.models.session import Session
    sa, student, sa_sess = _people(user_factory, session_factory)
    token = _token(_start(client, sa_sess.id, student.id))

    r = _get(client, f"{VIEW}/cabinet/superadmin/impersonate/enter?t={token}")
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet"
    imp = db.query(Session).filter(Session.user_id == student.id).one()
    db.refresh(imp)
    assert imp.is_active is True
    assert r.cookies.get("session_id") == imp.id

    # Тот же токен второй раз — свой кабинет, нового пропуска нет.
    again = _get(client, f"{VIEW}/cabinet/superadmin/impersonate/enter?t={token}")
    assert again.headers["location"] == f"{MAIN}/cabinet"
    assert "session_id" not in again.cookies


def test_enter_is_not_served_on_main_address(client, view_mode, user_factory, session_factory):
    sa, student, sa_sess = _people(user_factory, session_factory)
    token = _token(_start(client, sa_sess.id, student.id))
    r = _get(client, f"/cabinet/superadmin/impersonate/enter?t={token}")
    assert r.status_code == 404


def test_forged_token_goes_home(client, view_mode):
    r = _get(client, f"{VIEW}/cabinet/superadmin/impersonate/enter?t=forged")
    assert r.headers["location"] == f"{MAIN}/cabinet"
    assert "session_id" not in r.cookies


def test_view_address_refuses_ordinary_session(client, view_mode, user_factory, session_factory):
    sa, _student, sa_sess = _people(user_factory, session_factory)
    r = _get(client, f"{VIEW}/cabinet", sa_sess.id)
    # Страница — уводим в свой кабинет на основном адресе, а не на вход.
    assert r.status_code == 302
    assert r.headers["location"] == f"{MAIN}/cabinet"
    api = _get(client, f"{VIEW}/cabinet", sa_sess.id, headers={"accept": "application/json"})
    assert api.status_code == 401


def test_main_session_keeps_working_while_view_is_open(client, view_mode, user_factory, session_factory):
    sa, student, sa_sess = _people(user_factory, session_factory)
    token = _token(_start(client, sa_sess.id, student.id))
    view_sess = _get(client, f"{VIEW}/cabinet/superadmin/impersonate/enter?t={token}").cookies["session_id"]

    # Обе вкладки живут одновременно: каждая со своим пропуском.
    own = _get(client, "/cabinet", sa_sess.id)
    assert own.headers["location"] == "/cabinet/superadmin"
    seen = _get(client, f"{VIEW}/cabinet", view_sess)
    assert seen.status_code in (302, 303, 307)
    assert seen.headers["location"].startswith("/cabinet")
    assert seen.headers["location"] != "/cabinet/superadmin"


def test_close_view_returns_to_card_and_kills_session(client, db, view_mode, user_factory, session_factory):
    from app.models.session import Session
    sa, student, sa_sess = _people(user_factory, session_factory)
    back = f"/cabinet/students?student={student.id}"
    token = _token(_start(client, sa_sess.id, student.id, return_to=back))
    entered = _get(client, f"{VIEW}/cabinet/superadmin/impersonate/enter?t={token}")
    view_sess = entered.cookies["session_id"]
    return_cookie = entered.cookies["impersonation_return"]

    client.cookies.clear()
    r = client.post(
        f"{VIEW}/cabinet/superadmin/impersonate/stop",
        headers={"cookie": f"session_id={view_sess}; impersonation_return={return_cookie}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"{MAIN}{back}"
    imp = db.query(Session).filter(Session.id == view_sess).one()
    db.refresh(imp)
    assert imp.is_active is False

    # Свежий токен закрытую сессию не оживит: срок ушёл в прошлое.
    again = _get(client, f"{VIEW}/cabinet/superadmin/impersonate/enter?t={token}")
    assert again.headers["location"] == f"{MAIN}/cabinet"
    db.refresh(imp)
    assert imp.is_active is False


def test_handoff_token_expires(client, db, view_mode, user_factory, session_factory, monkeypatch):
    from app.api import cabinet_superadmin
    sa, student, sa_sess = _people(user_factory, session_factory)
    token = _token(_start(client, sa_sess.id, student.id))
    monkeypatch.setattr(cabinet_superadmin, "_VIEW_HANDOFF_MAX_AGE", -1)
    r = _get(client, f"{VIEW}/cabinet/superadmin/impersonate/enter?t={token}")
    assert r.headers["location"] == f"{MAIN}/cabinet"


def test_login_buttons_open_new_tab_without_question(client, view_mode, user_factory, session_factory):
    sa, student, sa_sess = _people(user_factory, session_factory)
    curator = user_factory(vk_id=910_003, name="Curator View", role_name="куратор")
    html = _get(client, "/cabinet/superadmin", sa_sess.id).text
    form = f'action="/cabinet/superadmin/impersonate/{curator.id}"'
    assert form in html
    tag = html[html.index(form):html.index(">", html.index(form))]
    assert 'target="_blank"' in tag
    assert "data-confirm" not in tag

    # Карточка ученика в «Людях» уводит в «Учеников» — кнопку смотрим у куратора.
    card = _get(client, f"/cabinet/superadmin/users/{curator.id}", sa_sess.id).text
    tag = card[card.index('id="impersonateForm"'):]
    assert 'target="_blank"' in tag[:tag.index(">")]

    students = _get(client, "/cabinet/students", sa_sess.id).text
    tag = students[students.index('id="impersonate-form"'):]
    assert "data-new-tab" in tag[:tag.index(">")]


def test_old_mode_keeps_same_tab_and_question(client, user_factory, session_factory):
    sa, _student, sa_sess = _people(user_factory, session_factory)
    curator = user_factory(vk_id=910_004, name="Curator Old", role_name="куратор")
    html = _get(client, "/cabinet/superadmin", sa_sess.id).text
    form = f'action="/cabinet/superadmin/impersonate/{curator.id}"'
    tag = html[html.index(form):html.index(">", html.index(form))]
    assert 'target="_blank"' not in tag
    assert "data-confirm" in tag
