"""Tests for /cabinet/superadmin/tags — admin/superadmin student tagging tool."""
import pytest

from app.constants import MOCK_SUBJECTS
from app.models.tag import Tag, UserTag
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.tags import get_suggested_tags


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def admin_rank4_client(client, user_factory, session_factory):
    user = user_factory(vk_id=910001, name="Admin Lisa", role_name="админ")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


@pytest.fixture()
def superadmin_client(client, user_factory, session_factory):
    user = user_factory(vk_id=910002, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


@pytest.fixture()
def student_user(user_factory):
    user = user_factory(vk_id=910010, name="Student One", role_name="ученик")
    user.first_name = "Анна"
    user.last_name = "Иванова"
    return user


@pytest.fixture()
def hidden_student_user(user_factory):
    """Student without course_periods/lessons_count — hidden by default."""
    user = user_factory(
        vk_id=910011, name="Hidden Student", role_name="ученик",
        profile_completed=False,
    )
    user.first_name = "Скрытый"
    user.last_name = "Ученик"
    return user


# ---------------------------------------------------------------------------
# GET /cabinet/superadmin/tags — access control
# ---------------------------------------------------------------------------

def test_tags_page_loads_for_admin(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    resp = client.get("/cabinet/superadmin/tags")
    assert resp.status_code == 200
    assert "Иванова" in resp.text


def test_tags_page_loads_for_superadmin(superadmin_client, student_user):
    client, _ = superadmin_client
    resp = client.get("/cabinet/superadmin/tags")
    assert resp.status_code == 200
    assert "Иванова" in resp.text


def test_tags_page_denied_for_curator(client, db, user_factory, session_factory):
    user = user_factory(vk_id=910020, name="Curator", role_name="куратор")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/superadmin/tags", follow_redirects=False)
    assert resp.status_code == 403


def test_tags_page_denied_for_student(client, db, user_factory, session_factory):
    user = user_factory(vk_id=910021, name="Student", role_name="ученик")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/superadmin/tags", follow_redirects=False)
    assert resp.status_code == 403


def test_tags_page_denied_no_session(client):
    resp = client.get("/cabinet/superadmin/tags", follow_redirects=False)
    assert resp.status_code in (302, 401)


# ---------------------------------------------------------------------------
# Visibility filter (course_periods/lessons_count)
# ---------------------------------------------------------------------------

def test_hidden_student_not_shown_by_default(admin_rank4_client, hidden_student_user):
    client, _ = admin_rank4_client
    resp = client.get("/cabinet/superadmin/tags")
    assert resp.status_code == 200
    assert "Скрытый" not in resp.text


def test_hidden_student_not_shown_for_admin_with_show_hidden(admin_rank4_client, hidden_student_user):
    """show_hidden is only honored for rank>=5."""
    client, _ = admin_rank4_client
    resp = client.get("/cabinet/superadmin/tags?show_hidden=1")
    assert resp.status_code == 200
    assert "Скрытый" not in resp.text


def test_hidden_student_shown_for_superadmin_with_show_hidden(superadmin_client, hidden_student_user):
    client, _ = superadmin_client
    resp = client.get("/cabinet/superadmin/tags?show_hidden=1")
    assert resp.status_code == 200
    assert "Скрытый" in resp.text


# ---------------------------------------------------------------------------
# Search (q) — by name and Telegram username
# ---------------------------------------------------------------------------

def test_search_by_name(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    resp = client.get("/cabinet/superadmin/tags?q=Иванова")
    assert resp.status_code == 200
    assert "Иванова" in resp.text


def test_search_by_telegram_username_with_at(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    student_user.tg_username = "bebebe5208"
    resp = client.get("/cabinet/superadmin/tags?q=@bebebe5208")
    assert resp.status_code == 200
    assert "Иванова" in resp.text


def test_search_by_telegram_username_without_at(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    student_user.tg_username = "bebebe5208"
    resp = client.get("/cabinet/superadmin/tags?q=bebebe5208")
    assert resp.status_code == 200
    assert "Иванова" in resp.text


def test_search_excludes_non_matching(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    student_user.tg_username = "bebebe5208"
    resp = client.get("/cabinet/superadmin/tags?q=несуществующий")
    assert resp.status_code == 200
    assert "Иванова" not in resp.text


# ---------------------------------------------------------------------------
# POST /cabinet/superadmin/tags/{user_id} — add tag
# ---------------------------------------------------------------------------

def test_add_tag_creates_tag_and_link(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "Рисунок", "csrf_token": "bypass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["tag"]["name"] == "Рисунок"

    tag = db.query(Tag).filter(Tag.name == "Рисунок").first()
    assert tag is not None
    link = db.get(UserTag, (student_user.id, tag.id))
    assert link is not None


def test_add_tag_case_insensitive_no_duplicate(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    resp1 = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "Рисунок", "csrf_token": "bypass"},
    )
    resp2 = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "рисунок", "csrf_token": "bypass"},
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["tag"]["id"] == resp2.json()["tag"]["id"]

    all_tags = db.query(Tag).all()
    assert sum(1 for t in all_tags if t.name.lower() == "рисунок") == 1
    assert db.query(UserTag).filter(UserTag.user_id == student_user.id).count() == 1


def test_add_tag_empty_name_400(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "   ", "csrf_token": "bypass"},
    )
    assert resp.status_code == 400


def test_add_tag_too_long_400(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "a" * 51, "csrf_token": "bypass"},
    )
    assert resp.status_code == 400


def test_add_tag_nonexistent_user_404(admin_rank4_client):
    client, _ = admin_rank4_client
    resp = client.post(
        "/cabinet/superadmin/tags/999999",
        data={"name": "Рисунок", "csrf_token": "bypass"},
    )
    assert resp.status_code == 404


def test_add_tag_denied_for_curator(client, db, user_factory, session_factory, student_user):
    user = user_factory(vk_id=910030, name="Curator", role_name="куратор")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "Рисунок", "csrf_token": "bypass"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /cabinet/superadmin/tags/{user_id}/{tag_id} — remove tag
# ---------------------------------------------------------------------------

def test_remove_tag(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    add_resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "Композиция", "csrf_token": "bypass"},
    )
    tag_id = add_resp.json()["tag"]["id"]

    del_resp = client.request(
        "DELETE", f"/cabinet/superadmin/tags/{student_user.id}/{tag_id}",
        headers={"X-CSRF-Token": "bypass"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True
    assert db.get(UserTag, (student_user.id, tag_id)) is None


def test_remove_tag_idempotent(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    resp = client.request(
        "DELETE", f"/cabinet/superadmin/tags/{student_user.id}/999999",
        headers={"X-CSRF-Token": "bypass"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# get_suggested_tags
# ---------------------------------------------------------------------------

def test_suggested_tags_include_mock_subjects_and_curators(db, user_factory):
    curator = user_factory(vk_id=910040, name="Куратор Петров", role_name="куратор")

    suggestions = get_suggested_tags(db)

    for subject in MOCK_SUBJECTS:
        assert subject in suggestions
    assert curator.name in suggestions


# ---------------------------------------------------------------------------
# ensure_profile_tags — auto-tags from registration choices / case growth
# ---------------------------------------------------------------------------

def test_profile_tags_auto_created_on_page_load(admin_rank4_client, db, student_user):
    """student_user has tariff=УВЕРЕННЫЙ, course_periods="10-14 июня", lessons_count="8"."""
    client, _ = admin_rank4_client
    resp = client.get("/cabinet/superadmin/tags")
    assert resp.status_code == 200

    tag_names = {t.name for t in db.query(Tag).all()}
    assert {"10-14", "8", "УВЕРЕННЫЙ"} <= tag_names

    linked_names = {
        tag.name for tag in db.query(Tag)
        .join(UserTag, UserTag.tag_id == Tag.id)
        .filter(UserTag.user_id == student_user.id)
        .all()
    }
    assert {"10-14", "8", "УВЕРЕННЫЙ"} <= linked_names

    for name in ("10-14", "8", "УВЕРЕННЫЙ"):
        assert name in resp.text


def test_profile_tags_idempotent_no_duplicates(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    client.get("/cabinet/superadmin/tags")
    client.get("/cabinet/superadmin/tags")

    assert sum(1 for t in db.query(Tag).all() if t.name == "8") == 1
    tag_id = db.query(Tag.id).filter(Tag.name == "8").scalar()
    assert db.query(UserTag).filter(
        UserTag.user_id == student_user.id, UserTag.tag_id == tag_id
    ).count() == 1


def test_profile_tag_removal_does_not_change_user_fields(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    client.get("/cabinet/superadmin/tags")

    tariff_tag = db.query(Tag).filter(Tag.name == "УВЕРЕННЫЙ").first()
    del_resp = client.request(
        "DELETE", f"/cabinet/superadmin/tags/{student_user.id}/{tariff_tag.id}",
        headers={"X-CSRF-Token": "bypass"},
    )
    assert del_resp.status_code == 200
    assert db.get(UserTag, (student_user.id, tariff_tag.id)) is None

    db.refresh(student_user)
    assert student_user.tariff == "УВЕРЕННЫЙ"


def test_profile_tags_include_kejs_on_score_growth(admin_rank4_client, db, student_user):
    db.add_all([
        Work(
            user_id=student_user.id, work_type=WORK_TYPE_MOCK_EXAM, status="success",
            month="январь", year=2026, filename="a.jpg", subject="Рисунок", score=50,
        ),
        Work(
            user_id=student_user.id, work_type=WORK_TYPE_MOCK_EXAM, status="success",
            month="февраль", year=2026, filename="b.jpg", subject="Рисунок", score=70,
        ),
    ])
    db.commit()

    client, _ = admin_rank4_client
    resp = client.get("/cabinet/superadmin/tags")
    assert resp.status_code == 200

    assert db.query(Tag).filter(Tag.name == "КЕЙС").first() is not None
    assert "КЕЙС" in resp.text
