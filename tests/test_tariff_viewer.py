"""Аккаунт просмотра одного тарифа (владелец 10.10.2026).

Куратору Алёне нужен отдельный вход по паролю, где видны все ученики
«Уверенного максимума». Аккаунт — модератор с `User.viewer_tariff`: видит
только учеников этого тарифа, без архива и статистики, и ничего не меняет.
Граница по тарифу держится в `student_access.get_student_for_staff_access`
(каждая вкладка карточки) и в выборке списка учеников.
"""

from datetime import datetime, timezone

import pytest

from app.constants import TARIFF_CONFIDENT_MAX, TARIFF_SELF
from app.models.user import User


@pytest.fixture()
def viewer(db, user_factory):
    user = user_factory(vk_id=991_001, name="Алёна Просмотр", role_name="модератор")
    user.viewer_tariff = TARIFF_CONFIDENT_MAX
    db.commit()
    return user


@pytest.fixture()
def viewer_client(client, session_factory, viewer):
    client.cookies.set("session_id", session_factory(viewer).id)
    return client


@pytest.fixture()
def students(db, user_factory):
    curator = user_factory(vk_id=991_010, name="Куратор Другой", role_name="куратор")
    ours = user_factory(vk_id=991_011, name="Ученик Максимум", tariff=TARIFF_CONFIDENT_MAX)
    other = user_factory(vk_id=991_012, name="Ученик Сам", tariff=TARIFF_SELF)
    ours.curator_id = curator.id
    db.commit()
    return ours, other


JSON = {"Accept": "application/json"}


def test_list_shows_only_viewer_tariff(viewer_client, students):
    ours, other = students
    page = viewer_client.get("/cabinet/students")
    assert page.status_code == 200
    assert "Ученик Максимум" in page.text
    assert "Ученик Сам" not in page.text
    # Кнопок тарифов нет — в списке он один; заголовок — сам тариф.
    assert 'class="tariff-pills"' not in page.text
    assert "Уверенный максимум" in page.text


def test_card_of_own_tariff_opens_other_is_403(viewer_client, students):
    ours, other = students
    assert viewer_client.get(f"/cabinet/students/{ours.id}/profile", headers=JSON).status_code == 200
    for tab in ("profile", "tasks", "portfolio", "statistics", "row", "legacy-portfolio"):
        resp = viewer_client.get(f"/cabinet/students/{other.id}/{tab}", headers=JSON)
        assert resp.status_code == 403, tab


def test_archive_closed_even_for_archived_student_of_same_tariff(viewer_client, db, user_factory):
    archived = user_factory(vk_id=991_020, name="Ученик Архив", tariff=TARIFF_CONFIDENT_MAX)
    archived.archived_at = datetime.now(timezone.utc)
    archived.is_active = False
    db.commit()
    assert viewer_client.get("/cabinet/archive", follow_redirects=False).status_code == 403
    resp = viewer_client.get(f"/cabinet/students/{archived.id}/profile", headers=JSON)
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/cabinet/superadmin/activity",
        "/cabinet/superadmin/stats",
        "/cabinet/admin/registration-stats.xlsx",
        "/cabinet/students?tab=statistics",
    ],
)
def test_statistics_closed(viewer_client, path):
    assert viewer_client.get(path, follow_redirects=False).status_code == 403


def test_home_and_menu_lead_only_to_students(viewer_client):
    home = viewer_client.get("/cabinet", follow_redirects=False)
    assert home.status_code == 302
    assert home.headers["location"] == "/cabinet/students"
    page = viewer_client.get("/cabinet/students")
    assert 'href="/cabinet/archive"' not in page.text
    assert 'href="/cabinet/superadmin/activity"' not in page.text


def test_cannot_change_student(viewer_client, db, students):
    ours, _ = students
    resp = viewer_client.post(
        f"/cabinet/students/{ours.id}/profile",
        data={"first_name": "Взлом"},
        headers=JSON,
    )
    assert resp.status_code == 403
    db.refresh(ours)
    assert ours.first_name != "Взлом"


def test_personal_section_rules_do_not_widen_viewer(viewer_client, db, viewer):
    """Правило из «Доступов» такому аккаунту не применяется: расширить его
    можно только сняв тариф."""
    from app.models.section_access import SectionAccessRule

    db.add(SectionAccessRule(section_key="archive", user_id=viewer.id, level="view"))
    db.commit()
    assert viewer_client.get("/cabinet/archive", follow_redirects=False).status_code == 403


def test_tariff_ignored_for_non_moderator(client, db, user_factory, session_factory, students):
    """Поле действует только у модератора: у ГП с заполненным полем список целый."""
    head = user_factory(vk_id=991_030, name="ГП", role_name="админ", is_admin=True)
    head.viewer_tariff = TARIFF_CONFIDENT_MAX
    db.commit()
    client.cookies.set("session_id", session_factory(head).id)
    page = client.get("/cabinet/students")
    assert "Ученик Сам" in page.text


# ── Создание аккаунта суперадмином ───────────────────────────────────────────

@pytest.fixture()
def superadmin_client(client, user_factory, session_factory):
    sa = user_factory(vk_id=991_040, name="Суперадмин", role_name="суперадмин")
    client.cookies.set("session_id", session_factory(sa).id)
    return client


def _create(client, role_id, viewer_tariff, first_name):
    return client.post(
        "/cabinet/superadmin/users/create-staff",
        data={
            "first_name": first_name, "last_name": "Просмотр",
            "role_id": str(role_id), "viewer_tariff": viewer_tariff,
            "csrf_token": "bypass",
        },
    )


def test_superadmin_creates_tariff_viewer(superadmin_client, db, role_factory):
    moderator_role = role_factory("модератор", 3)
    resp = _create(superadmin_client, moderator_role.id, TARIFF_CONFIDENT_MAX, "Алёна")
    assert resp.status_code == 200
    assert "Доступ создан" in resp.text
    staff = db.query(User).filter(User.first_name == "Алёна").one()
    assert staff.viewer_tariff == TARIFF_CONFIDENT_MAX
    assert staff.staff_login and staff.password_hash
    assert staff.telegram_chat_id is None


def test_tariff_only_for_moderator(superadmin_client, db, role_factory):
    curator_role = role_factory("куратор", 2)
    resp = _create(superadmin_client, curator_role.id, TARIFF_CONFIDENT_MAX, "Пётр")
    assert "только для роли «модератор»" in resp.text
    assert db.query(User).filter(User.first_name == "Пётр").first() is None


def test_unknown_tariff_refused(superadmin_client, db, role_factory):
    moderator_role = role_factory("модератор", 3)
    resp = _create(superadmin_client, moderator_role.id, "МАКСИМУМ", "Олег")
    assert "Тариф не найден" in resp.text
    assert db.query(User).filter(User.first_name == "Олег").first() is None


def test_superadmin_links_viewer_to_curator(superadmin_client, db, role_factory, user_factory):
    curator = user_factory(vk_id=991_041, name="Алёна Куратор", role_name="куратор")
    moderator_role = role_factory("модератор", 3)
    superadmin_client.post(
        "/cabinet/superadmin/users/create-staff",
        data={
            "first_name": "Связь", "role_id": str(moderator_role.id),
            "viewer_tariff": TARIFF_CONFIDENT_MAX, "viewer_owner_id": str(curator.id),
            "csrf_token": "bypass",
        },
    )
    staff = db.query(User).filter(User.first_name == "Связь").one()
    assert staff.viewer_owner_id == curator.id


def test_curator_button_needs_tariff(superadmin_client, db, role_factory, user_factory):
    curator = user_factory(vk_id=991_042, name="Куратор Без", role_name="куратор")
    moderator_role = role_factory("модератор", 3)
    resp = superadmin_client.post(
        "/cabinet/superadmin/users/create-staff",
        data={
            "first_name": "Безтарифа", "role_id": str(moderator_role.id),
            "viewer_owner_id": str(curator.id), "csrf_token": "bypass",
        },
    )
    assert "только для аккаунта с тарифом" in resp.text
    assert db.query(User).filter(User.first_name == "Безтарифа").first() is None


# ── Кнопка в кабинете куратора: пароль, вход, возврат ────────────────────────

PASSWORD = "секрет-просмотра"


@pytest.fixture()
def linked(db, user_factory, viewer):
    import bcrypt

    curator = user_factory(vk_id=991_050, name="Алёна", role_name="куратор")
    viewer.viewer_owner_id = curator.id
    viewer.staff_login = "alena.view"
    viewer.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    db.commit()
    return curator, viewer


@pytest.fixture()
def curator_client(client, session_factory, linked):
    curator, _ = linked
    session = session_factory(curator)
    client.cookies.set("session_id", session.id)
    return client, session


def _enter(client, password):
    return client.post(
        "/cabinet/tariff-view",
        data={"password": password, "csrf_token": "bypass"},
        follow_redirects=False,
    )


def _adopt(client, resp):
    """Куки ответа — в клиент одним экземпляром: иначе выставленная руками
    `session_id` и пришедшая от сервера лежат в банке рядом как две разные."""
    for name, value in resp.cookies.items():
        client.cookies.delete(name)
        client.cookies.set(name, value)


def _exit(client):
    resp = client.post("/cabinet/tariff-view/exit", data={"csrf_token": "bypass"}, follow_redirects=False)
    _adopt(client, resp)
    return resp


def test_button_only_for_linked_curator(curator_client, client, session_factory, user_factory):
    c, _ = curator_client
    page = c.get("/cabinet/curator")
    assert 'href="/cabinet/tariff-view"' in page.text
    assert "Уверенный максимум" in page.text

    other = user_factory(vk_id=991_051, name="Другой куратор", role_name="куратор")
    c.cookies.set("session_id", session_factory(other).id)
    assert 'href="/cabinet/tariff-view"' not in c.get("/cabinet/curator").text
    assert c.get("/cabinet/tariff-view").status_code == 404
    assert _enter(c, PASSWORD).status_code == 404


def test_wrong_password_keeps_curator_session(curator_client):
    c, session = curator_client
    resp = _enter(c, "не тот")
    assert resp.status_code == 200
    assert "Неверный пароль" in resp.text
    assert c.cookies.get("session_id") == session.id


def test_enter_and_return(curator_client, db, students, linked):
    c, curator_session = curator_client
    _, viewer = linked
    resp = _enter(c, PASSWORD)
    assert resp.status_code == 303
    _adopt(c, resp)
    viewer_session_id = c.cookies.get("session_id")
    assert viewer_session_id != curator_session.id

    # Внутри — аккаунт просмотра: только тариф и кнопка обратно.
    home = c.get("/cabinet", follow_redirects=False)
    assert home.headers["location"] == "/cabinet/students"
    page = c.get("/cabinet/students")
    assert "Ученик Максимум" in page.text and "Ученик Сам" not in page.text
    assert "В кабинет куратора" in page.text

    back = _exit(c)
    assert back.status_code == 303
    assert c.cookies.get("session_id") == curator_session.id
    assert c.get("/cabinet/curator").status_code == 200

    from app.models.session import Session
    db.expire_all()
    assert db.get(Session, viewer_session_id).is_active is False


def test_forged_return_cookie_logs_out(curator_client, db):
    c, curator_session = curator_client
    _adopt(c, _enter(c, PASSWORD))
    c.cookies.delete("tariff_view_return")
    c.cookies.set("tariff_view_return", "forged")
    back = _exit(c)
    assert back.status_code == 303
    assert back.headers["location"] == "/"
    assert c.cookies.get("session_id") != curator_session.id


def test_return_refused_when_curator_session_ended(curator_client, db):
    c, curator_session = curator_client
    _adopt(c, _enter(c, PASSWORD))
    curator_session.is_active = False
    db.commit()
    back = _exit(c)
    assert back.headers["location"] == "/"


def test_exit_is_404_for_non_viewer(curator_client):
    c, session = curator_client
    resp = c.post("/cabinet/tariff-view/exit", data={"csrf_token": "bypass"}, follow_redirects=False)
    assert resp.status_code == 404
    assert c.cookies.get("session_id") == session.id


def test_password_login_page_unchanged(client):
    page = client.get("/login")
    assert 'name="login"' in page.text
    assert "Вход по логину и паролю" in page.text


def test_superadmin_eyes_sees_button_without_link(client, db, session_factory, user_factory, linked):
    """Суперадмин «глазами» куратора видит кнопку, но перейти по ней не может."""
    curator, _ = linked
    sa = user_factory(vk_id=991_060, name="СА", role_name="суперадмин")
    session = session_factory(curator)
    session.impersonated_by_id = sa.id
    db.commit()
    client.cookies.set("session_id", session.id)
    page = client.get("/cabinet/curator")
    assert "Уверенный максимум" in page.text
    assert "вход по ней закрыт" in page.text
    assert 'href="/cabinet/tariff-view"' not in page.text
    assert client.get("/cabinet/tariff-view").status_code == 403
