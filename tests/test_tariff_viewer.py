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
        "/cabinet/admin/registration-stats.csv",
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
