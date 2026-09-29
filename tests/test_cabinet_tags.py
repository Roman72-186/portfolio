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
    """course_periods="10-14 июня", lessons_count="8"; тариф — действующий
    (тег отработавшего автотег не ставит, владелец 29.09.2026)."""
    client, _ = admin_rank4_client
    student_user.tariff = "Я С ВАМИ"
    db.commit()
    resp = client.get("/cabinet/superadmin/tags")
    assert resp.status_code == 200

    tag_names = {t.name for t in db.query(Tag).all()}
    assert {"10-14", "8", "Я С ВАМИ"} <= tag_names

    linked_names = {
        tag.name for tag in db.query(Tag)
        .join(UserTag, UserTag.tag_id == Tag.id)
        .filter(UserTag.user_id == student_user.id)
        .all()
    }
    assert {"10-14", "8", "Я С ВАМИ"} <= linked_names

    for name in ("10-14", "8", "Я С ВАМИ"):
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
    student_user.tariff = "Я С ВАМИ"
    db.commit()
    client.get("/cabinet/superadmin/tags")

    tariff_tag = db.query(Tag).filter(Tag.name == "Я С ВАМИ").first()
    del_resp = client.request(
        "DELETE", f"/cabinet/superadmin/tags/{student_user.id}/{tariff_tag.id}",
        headers={"X-CSRF-Token": "bypass"},
    )
    assert del_resp.status_code == 200
    assert db.get(UserTag, (student_user.id, tariff_tag.id)) is None

    db.refresh(student_user)
    assert student_user.tariff == "Я С ВАМИ"


# ---------------------------------------------------------------------------
# POST /cabinet/superadmin/tags/bulk-lookup — bulk Р/К/Р+К checkbox table
# ---------------------------------------------------------------------------

def test_bulk_lookup_matches_and_reports_not_found(admin_rank4_client, student_user):
    client, _ = admin_rank4_client
    student_user.tg_username = "bebebe5208"

    resp = client.post(
        "/cabinet/superadmin/tags/bulk-lookup",
        data={"usernames": "@bebebe5208\n@nosuchuser", "csrf_token": "bypass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["matched"]) == 1
    matched = body["matched"][0]
    assert matched["user_id"] == student_user.id
    assert matched["username"] == "bebebe5208"
    assert matched["tags"] == {"Р": None, "К": None, "Р+К": None}
    assert body["not_found"] == ["nosuchuser"]


def test_bulk_lookup_reflects_existing_tags(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    student_user.tg_username = "bebebe5208"

    add_resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "Р", "csrf_token": "bypass"},
    )
    tag_id = add_resp.json()["tag"]["id"]

    resp = client.post(
        "/cabinet/superadmin/tags/bulk-lookup",
        data={"usernames": "bebebe5208", "csrf_token": "bypass"},
    )
    assert resp.status_code == 200
    matched = resp.json()["matched"][0]
    assert matched["tags"]["Р"] == tag_id
    assert matched["tags"]["К"] is None


def test_bulk_lookup_empty_input(admin_rank4_client):
    client, _ = admin_rank4_client
    resp = client.post(
        "/cabinet/superadmin/tags/bulk-lookup",
        data={"usernames": "   ", "csrf_token": "bypass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "matched": [], "not_found": []}


def test_bulk_lookup_denied_for_curator(client, db, user_factory, session_factory, student_user):
    user = user_factory(vk_id=910031, name="Curator", role_name="куратор")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.post(
        "/cabinet/superadmin/tags/bulk-lookup",
        data={"usernames": "bebebe5208", "csrf_token": "bypass"},
    )
    assert resp.status_code == 403


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


# ---------------------------------------------------------------------------
# Кому можно ставить свободный тег (код-ревью 28.09.2026, P3)
# ---------------------------------------------------------------------------
# Экран тегов показывает только учеников, а маршруты принимали любой user_id:
# тег вешался на куратора, на архивного и удалённого ученика, а снятие цель не
# проверяло вовсе. Архив по правилу 8 `AGENTS.md` открыт только на чтение.

def _archive(db, user):
    from datetime import datetime, timezone

    user.archived_at = datetime.now(timezone.utc)
    user.is_active = False
    db.commit()


def _soft_delete(db, user):
    from datetime import datetime, timezone

    user.deleted_at = datetime.now(timezone.utc)
    db.commit()


def _link(db, user, name="Композиция"):
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.add(UserTag(user_id=user.id, tag_id=tag.id))
    db.commit()
    return tag


def test_archived_student_tags_are_read_only(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    tag = _link(db, student_user)
    _archive(db, student_user)

    add = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "Новый", "csrf_token": "bypass"},
    )
    remove = client.request(
        "DELETE", f"/cabinet/superadmin/tags/{student_user.id}/{tag.id}",
        headers={"X-CSRF-Token": "bypass"},
    )

    assert add.status_code == 409, add.text
    assert remove.status_code == 409, remove.text
    assert db.get(UserTag, (student_user.id, tag.id)) is not None
    assert db.query(UserTag).filter(UserTag.user_id == student_user.id).count() == 1


@pytest.mark.parametrize("target", ["curator", "deleted"])
def test_tags_only_for_live_students(
    admin_rank4_client, db, user_factory, student_user, target
):
    client, _ = admin_rank4_client
    if target == "curator":
        victim = user_factory(vk_id=910020, name="Curator", role_name="куратор")
    else:
        victim = student_user
    tag = _link(db, victim)
    if target == "deleted":
        _soft_delete(db, victim)

    add = client.post(
        f"/cabinet/superadmin/tags/{victim.id}",
        data={"name": "Новый", "csrf_token": "bypass"},
    )
    remove = client.request(
        "DELETE", f"/cabinet/superadmin/tags/{victim.id}/{tag.id}",
        headers={"X-CSRF-Token": "bypass"},
    )

    assert add.status_code == 404, add.text
    assert remove.status_code == 404, remove.text
    assert db.get(UserTag, (victim.id, tag.id)) is not None


def test_blocked_student_still_tagged(admin_rank4_client, db, student_user):
    """Блокировка без архива — не «только чтение»: карточка её так же
    пропускает, а на доступ теги не влияют."""
    client, _ = admin_rank4_client
    student_user.is_active = False
    db.commit()

    resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "Композиция", "csrf_token": "bypass"},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Скрытые теги (владелец 29.09.2026: июньские теги скрыть)
# ---------------------------------------------------------------------------
# Все 18 тегов прода заведены 11–15.06.2026 под прошлый поток: 530 привязок
# из 548 — у архива. Скрытие — только показ: выпадающие списки, подсказки,
# чипы учеников. Доступ по тегам (`mock_exam_access`, адресация заданий) не
# меняется, привязки в базе остаются.

def _hidden_tag(db, name):
    tag = Tag(name=name, is_hidden=True)
    db.add(tag)
    db.commit()
    return tag


def test_hidden_tag_is_left_out_of_lists_and_chips(db, student_user):
    from app.services.tags import get_all_tags, get_tags_for_users

    hidden = _hidden_tag(db, "15-20")
    visible = Tag(name="Сентябрь")
    db.add(visible)
    db.commit()
    db.add_all([
        UserTag(user_id=student_user.id, tag_id=hidden.id),
        UserTag(user_id=student_user.id, tag_id=visible.id),
    ])
    db.commit()

    assert [t.name for t in get_all_tags(db)] == ["Сентябрь"]
    assert "15-20" not in get_suggested_tags(db)
    assert [t.name for t in get_tags_for_users(db, [student_user.id])[student_user.id]] == [
        "Сентябрь"
    ]
    # Привязка в базе цела — скрыт только показ.
    assert db.get(UserTag, (student_user.id, hidden.id)) is not None


def test_auto_tagging_brings_back_current_tariff_tag(db, student_user):
    """Действующий тариф возвращает тег: у 46 живых учеников «Я С ВАМИ», а тег
    с этим именем июньский. Пропусти его автотег — тарифные теги получили бы
    38 учеников на новых именах, а эти 46 ничего. Имя уникально."""
    from app.constants import TARIFF_WITH_YOU
    from app.services.tags import ensure_profile_tags

    student_user.tariff = TARIFF_WITH_YOU
    db.commit()
    hidden = _hidden_tag(db, TARIFF_WITH_YOU)

    ensure_profile_tags(db, [student_user])

    db.refresh(hidden)
    assert hidden.is_hidden is False
    assert db.get(UserTag, (student_user.id, hidden.id)) is not None


@pytest.mark.parametrize("with_june_tag", [True, False])
def test_legacy_tariff_tag_stays_in_archive(db, student_user, with_june_tag):
    """Отработавший тариф (владелец 29.09.2026: «должны быть только у архивных
    учеников и никак не должны фигурировать у нас»): его тег не возвращается,
    не ставится и не заводится заново. Живой ученик со старым тарифом бывает —
    «служба заботы» получила «УВЕРЕННЫЙ» ORM-дефолтом при первом входе."""
    from app.services.tags import ensure_profile_tags

    assert student_user.tariff == "УВЕРЕННЫЙ"
    june = _hidden_tag(db, "УВЕРЕННЫЙ") if with_june_tag else None

    ensure_profile_tags(db, [student_user])

    names = [
        t.name for t in db.query(Tag).join(UserTag, UserTag.tag_id == Tag.id)
        .filter(UserTag.user_id == student_user.id)
    ]
    assert "УВЕРЕННЫЙ" not in names
    if june is not None:
        db.refresh(june)
        assert june.is_hidden is True
    else:
        assert db.query(Tag).filter(Tag.name == "УВЕРЕННЫЙ").count() == 0


def test_manual_legacy_tariff_tag_is_refused(admin_rank4_client, db, student_user):
    client, _ = admin_rank4_client
    june = _hidden_tag(db, "МАКСИМУМ")

    resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "максимум", "csrf_token": "bypass"},
    )

    assert resp.status_code == 400, resp.text
    db.refresh(june)
    assert june.is_hidden is True
    assert db.get(UserTag, (student_user.id, june.id)) is None


def test_auto_tagging_keeps_june_period_tags_hidden(db, student_user):
    """Период и число уроков — поля июньской анкеты: одна запись с ними вернула
    бы в показ «10-14» и «10» всему экрану. Скрытый тег по ним не ставится."""
    from app.services.tags import ensure_profile_tags

    period = _hidden_tag(db, "10-14")
    lessons = _hidden_tag(db, student_user.lessons_count)

    ensure_profile_tags(db, [student_user])

    for tag in (period, lessons):
        db.refresh(tag)
        assert tag.is_hidden is True
        assert db.get(UserTag, (student_user.id, tag.id)) is None


def test_manual_tag_brings_hidden_tag_back(admin_rank4_client, db, student_user):
    """Сотрудник сам ставит тег с именем скрытого — это решение вернуть его в
    показ: имя уникально, второго «Композиция» не завести. Так же вернутся
    «Р»/«К»/«Р+К» при первой массовой постановке на экране тегов."""
    client, _ = admin_rank4_client
    hidden = _hidden_tag(db, "Композиция")

    resp = client.post(
        f"/cabinet/superadmin/tags/{student_user.id}",
        data={"name": "композиция", "csrf_token": "bypass"},
    )

    assert resp.status_code == 200, resp.text
    db.refresh(hidden)
    assert hidden.is_hidden is False
    assert db.get(UserTag, (student_user.id, hidden.id)) is not None
