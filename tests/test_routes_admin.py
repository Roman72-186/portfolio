"""Tests for /admin/* routes."""


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_admin_requires_auth(client):
    resp = client.get("/admin/users", follow_redirects=False)
    assert resp.status_code == 302


def test_admin_requires_admin_role(auth_client):
    client, _ = auth_client  # regular user, not admin
    resp = client.get("/admin/users", follow_redirects=False)
    assert resp.status_code == 403


def test_admin_page_accessible_to_admin(admin_client):
    client, _ = admin_client
    resp = client.get("/admin/users", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/cabinet/superadmin/users")


def test_admin_page_lists_users(admin_client, db, user_factory):
    client, _ = admin_client
    user_factory(vk_id=111_111, name="Студентка Ольга")

    resp = client.get("/cabinet/superadmin/users")
    assert "Студентка Ольга" in resp.text


def test_admin_page_filters_users_by_role(admin_client, db, user_factory):
    client, _ = admin_client
    user_factory(vk_id=111_112, name="Role Filter Student", role_name="ученик")
    user_factory(vk_id=111_113, name="Role Filter Curator", role_name="куратор")

    resp = client.get("/cabinet/superadmin/users?role_rank=2")

    assert resp.status_code == 200
    assert "Role Filter Curator" in resp.text
    assert "Role Filter Student" not in resp.text


def test_admin_page_filters_users_by_tag(admin_client, db, user_factory):
    from app.services.tags import add_tag_to_user, get_or_create_tag

    client, _ = admin_client
    tagged = user_factory(vk_id=111_114, name="Tag Filter Tagged", role_name="ученик")
    other = user_factory(vk_id=111_115, name="Tag Filter Other", role_name="ученик")

    tag = get_or_create_tag(db, "ОСОБЫЙ")
    add_tag_to_user(db, tagged.id, tag.id)
    db.commit()

    resp = client.get("/cabinet/superadmin/users")
    assert resp.status_code == 200
    assert f'value="{tag.id}"' in resp.text
    assert "ОСОБЫЙ" in resp.text

    resp = client.get(f"/cabinet/superadmin/users?tag={tag.id}")

    assert resp.status_code == 200
    assert "Tag Filter Tagged" in resp.text
    assert "Tag Filter Other" not in resp.text


# ---------------------------------------------------------------------------
# Tariff update
# ---------------------------------------------------------------------------

def test_update_tariff_changes_value(admin_client, db, user_factory):
    client, _ = admin_client
    target = user_factory(vk_id=222_222, tariff="УВЕРЕННЫЙ")

    resp = client.post(
        f"/admin/users/{target.id}/tariff",
        data={"tariff": "МАКСИМУМ"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db.refresh(target)
    assert target.tariff == "МАКСИМУМ"


def test_update_tariff_invalid_value_ignored(admin_client, db, user_factory):
    client, _ = admin_client
    target = user_factory(vk_id=333_333, tariff="УВЕРЕННЫЙ")

    client.post(
        f"/admin/users/{target.id}/tariff",
        data={"tariff": "INVALID_TARIFF"},
        follow_redirects=False,
    )
    db.refresh(target)
    assert target.tariff == "УВЕРЕННЫЙ"  # unchanged


# ---------------------------------------------------------------------------
# Issue one-time login link
# ---------------------------------------------------------------------------

def test_issue_link_for_active_member(admin_client, db, user_factory):
    client, _ = admin_client
    target = user_factory(vk_id=444_444, is_active=True, is_group_member=True)

    resp = client.post(f"/admin/users/{target.id}/issue-link", follow_redirects=False)
    assert resp.status_code == 200
    assert "/auth/link?token=" in resp.text


def test_issue_link_for_inactive_user_shows_error(admin_client, db, user_factory):
    client, _ = admin_client
    target = user_factory(vk_id=555_555, is_active=False)

    resp = client.post(f"/admin/users/{target.id}/issue-link", follow_redirects=False)
    assert resp.status_code == 200
    assert "неактивн" in resp.text.lower() or "нельзя" in resp.text.lower()


def test_issue_link_nonexistent_user_shows_error(admin_client):
    client, _ = admin_client
    resp = client.post("/admin/users/999999/issue-link", follow_redirects=False)
    assert resp.status_code == 200
    assert "не найден" in resp.text


# ---------------------------------------------------------------------------
# Toggle active / admin
# ---------------------------------------------------------------------------

def test_toggle_active_disables_user(admin_client, db, user_factory):
    client, _ = admin_client
    target = user_factory(vk_id=666_666, is_active=True)

    client.post(f"/admin/users/{target.id}/toggle-active", follow_redirects=False)
    db.refresh(target)
    assert target.is_active is False


def test_toggle_active_re_enables_user(admin_client, db, user_factory):
    client, _ = admin_client
    target = user_factory(vk_id=777_777, is_active=False)

    client.post(f"/admin/users/{target.id}/toggle-active", follow_redirects=False)
    db.refresh(target)
    assert target.is_active is True


def test_toggle_admin_grants_admin_role(admin_client, db, user_factory):
    client, _ = admin_client
    target = user_factory(vk_id=888_888, is_admin=False)

    client.post(f"/admin/users/{target.id}/toggle-admin", follow_redirects=False)
    db.refresh(target)
    assert target.is_admin is True


def test_admin_cannot_toggle_own_admin_status(admin_client, db):
    client, admin = admin_client

    client.post(f"/admin/users/{admin.id}/toggle-admin", follow_redirects=False)
    db.refresh(admin)
    assert admin.is_admin is True  # unchanged


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------

def test_assign_role_changes_user_role(admin_client, db, user_factory):
    from app.models.role import Role
    client, _ = admin_client
    target = user_factory(vk_id=111_222, role_name="ученик")

    # Create куратор role if not exists
    curator_role = db.query(Role).filter(Role.name == "куратор").first()
    if not curator_role:
        curator_role = Role(name="куратор", rank=2, display_name="Куратор")
        db.add(curator_role)
        db.commit()
        db.refresh(curator_role)

    resp = client.post(
        f"/admin/users/{target.id}/role",
        data={"role_id": str(curator_role.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    db.refresh(target)
    assert target.role_id == curator_role.id


def test_assign_role_cannot_change_own_role(admin_client, db):
    client, admin = admin_client
    original_role_id = admin.role_id

    client.post(
        f"/admin/users/{admin.id}/role",
        data={"role_id": "1"},
        follow_redirects=False,
    )
    db.refresh(admin)
    assert admin.role_id == original_role_id  # unchanged


# ---------------------------------------------------------------------------
# Bulk curator assignment
# ---------------------------------------------------------------------------

def test_bulk_assign_curator_from_admin_users(admin_client, db, user_factory):
    client, _ = admin_client
    curator = user_factory(vk_id=121_212, name="Curator One", role_name="куратор")
    student = user_factory(vk_id=131_313, name="Student One", role_name="ученик")
    student.tg_username = "student_one"
    db.commit()

    resp = client.post(
        "/admin/users/assign-curator-bulk",
        data={
            "curator_id": str(curator.id),
            "student_usernames": "@student_one",
            "cohort_tag": "may",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    db.refresh(student)
    assert student.curator_id == curator.id
    assert student.cohort_tag == "may"
