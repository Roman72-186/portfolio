"""Правки профиля из карточки пользователя: по рангу и не в архив.

Код-ревью 28.09.2026, P2 № 8: `/tags`, `/curator` и `/cohort-tag` в
`cabinet_superadmin.py` не звали `can_manage_user_by_rank` — Главный
преподаватель менял суперадмину куратора, «о себе» и метку набора. Там же
архивный пользователь правился задним числом (`AGENTS.md`, правило 8).
Свою карточку править по-прежнему можно: проверка ранга — про чужие профили.
"""
import pytest

from app.services.user_management import archive_user, soft_delete_user

ROUTES = [
    ("tags", {"about": "чужой текст", "cohort_tag": "", "curator_id": ""}),
    ("curator", {"curator_id": "{cid}"}),
    ("cohort-tag", {"cohort_tag": "{tag}"}),
]


@pytest.fixture()
def people(db, user_factory):
    superadmin = user_factory(vk_id=950_001, name="Супер", role_name="суперадмин")
    chief = user_factory(vk_id=950_002, name="ГП", role_name="админ")
    curator = user_factory(vk_id=950_003, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=950_004, name="Ученик")
    superadmin.about = "свой текст"
    db.commit()
    return superadmin, chief, curator, student


def _form(data, curator):
    from app.constants import COHORT_TAGS

    tag = sorted(COHORT_TAGS)[0]
    return {k: v.format(cid=curator.id, tag=tag) for k, v in data.items()}


def _snapshot(u):
    return (u.about, u.curator_id, u.cohort_tag)


@pytest.mark.parametrize("route,data", ROUTES, ids=[r for r, _ in ROUTES])
def test_chief_teacher_cannot_edit_superadmin_profile(
    client, db, session_factory, people, route, data
):
    superadmin, chief, curator, _ = people
    before = _snapshot(superadmin)
    client.cookies.set("session_id", session_factory(chief).id)

    resp = client.post(
        f"/cabinet/superadmin/users/{superadmin.id}/{route}",
        data=_form(data, curator),
        follow_redirects=False,
    )

    assert resp.status_code == 403, resp.text
    db.refresh(superadmin)
    assert _snapshot(superadmin) == before


@pytest.mark.parametrize("make_unwritable", [archive_user, soft_delete_user], ids=["archived", "deleted"])
@pytest.mark.parametrize("route,data", ROUTES, ids=[r for r, _ in ROUTES])
def test_archived_or_deleted_profile_is_read_only(
    client, db, session_factory, people, route, data, make_unwritable
):
    superadmin, _, curator, student = people
    assert make_unwritable(db, target_user_id=student.id, performed_by_id=superadmin.id)
    db.refresh(student)
    before = _snapshot(student)
    client.cookies.set("session_id", session_factory(superadmin).id)

    resp = client.post(
        f"/cabinet/superadmin/users/{student.id}/{route}",
        data=_form(data, curator),
        follow_redirects=False,
    )

    assert resp.status_code == 409, resp.text
    db.refresh(student)
    assert _snapshot(student) == before


@pytest.mark.parametrize("route,data", ROUTES, ids=[r for r, _ in ROUTES])
def test_chief_teacher_still_edits_student(client, db, session_factory, people, route, data):
    _, chief, curator, student = people
    client.cookies.set("session_id", session_factory(chief).id)

    resp = client.post(
        f"/cabinet/superadmin/users/{student.id}/{route}",
        data=_form(data, curator),
        follow_redirects=False,
    )

    assert resp.status_code in (200, 303), resp.text


def test_superadmin_still_edits_own_profile(client, db, session_factory, people):
    superadmin, _, _, _ = people
    client.cookies.set("session_id", session_factory(superadmin).id)

    resp = client.post(
        f"/cabinet/superadmin/users/{superadmin.id}/tags",
        data={"about": "новый текст"},
        follow_redirects=False,
    )

    assert resp.status_code == 303, resp.text
    db.refresh(superadmin)
    assert superadmin.about == "новый текст"
