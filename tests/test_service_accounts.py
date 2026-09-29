"""Служебные аккаунты — вне учёта, но с доступом ученика (владелец 29.09.2026).

«Никуда, никакой учёт не идут. Но я как ученик Роман Махметов могу смотреть и
тестировать всё». Служебные — 199 (ученический аккаунт владельца) и 277
(«служба заботы», тариф «УВЕРЕННЫЙ» у него остаётся). Механизм — прежний
`REPORT_EXCLUDED_USER_IDS`: отчётам он служил с 28.09, теперь им же закрыты
списки, очереди проверки, счётчики аудитории, фильтры тарифов и теги.

Доступ не трогается: задание «всем» служебному видно (`task_audience_user_ids`),
иначе владелец не увидел бы того, что видит ученик.
"""

import pytest

from app.constants import REPORT_EXCLUDED_USER_IDS
from app.models.user import User


@pytest.fixture()
def service_student(db, role_factory):
    """Служебный ученик с настоящим id 199 и старым тарифом, как у 277."""
    role = role_factory("ученик", 1)
    user = User(
        id=199, vk_id=199_199, name="Роман Махметов", first_name="Роман",
        last_name="Махметов", tariff="УВЕРЕННЫЙ", is_active=True,
        is_group_member=True, profile_completed=True, portfolio_do_completed=True,
        course_periods="10-14 июня", lessons_count="8", role_id=role.id,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture()
def real_student(user_factory):
    return user_factory(vk_id=910_264, name="Ксения Зайцева", tariff="Я С ВАМИ")


def _staff(user_factory, *, rank_name="суперадмин", vk_id=910_001):
    user = user_factory(vk_id=vk_id, name="Сотрудник", role_name=rank_name)
    rank = {"куратор": 2, "админ": 4, "суперадмин": 5}[rank_name]
    return user, {"user_id": user.id, "role_rank": rank}


def test_both_service_accounts_are_listed():
    assert {199, 277} <= REPORT_EXCLUDED_USER_IDS


def test_students_screen_hides_service_account(db, user_factory, service_student, real_student):
    from app.api.cabinet_students_shared import _get_accessible_students

    _, chief = _staff(user_factory)
    curator, curator_ctx = _staff(user_factory, rank_name="куратор", vk_id=910_002)
    service_student.curator_id = curator.id
    real_student.curator_id = curator.id
    db.commit()

    for ctx in (chief, curator_ctx):
        ids = {s.id for s in _get_accessible_students(ctx, db)}
        assert real_student.id in ids
        assert service_student.id not in ids

    from datetime import datetime, timezone
    service_student.archived_at = datetime.now(timezone.utc)
    service_student.is_active = False
    db.commit()
    assert service_student.id not in {
        s.id for s in _get_accessible_students(chief, db, archived=True)
    }


def test_review_queue_hides_service_account(db, user_factory, service_student, real_student):
    from app.services.review_aggregate import _accessible_students

    _, chief = _staff(user_factory)
    ids = {s.id for s in _accessible_students(db, chief)}
    assert real_student.id in ids
    assert service_student.id not in ids


def test_audience_count_skips_service_but_access_stays(db, user_factory, service_student, real_student):
    from app.services.tracker import count_task_audience, create_task, task_audience_user_ids
    from app.services.video_topics import count_topic_audience  # noqa: F401 — тот же фильтр

    staff, _ = _staff(user_factory)
    assert count_task_audience(db, assign_to_all=True, tag_ids=[], assignee_ids=[]) == 1

    task = create_task(
        db, title="Эскиз", user_id=staff.id, kind="material", assign_to_all=True,
    )
    task.is_published = True
    db.commit()
    # Доступ: служебный видит задание «всем» — владелец тестирует как ученик.
    assert service_student.id in task_audience_user_ids(db, task.id)


def test_tariff_filter_ignores_service_account(db, service_student, real_student):
    from app.services.user_management import tariffs_in_use

    assert "УВЕРЕННЫЙ" not in tariffs_in_use(db)
    assert "Я С ВАМИ" in tariffs_in_use(db)


def test_tags_screen_skips_service_account(client, db, user_factory, session_factory, service_student):
    from app.models.tag import UserTag

    staff, _ = _staff(user_factory)
    client.cookies.set("session_id", session_factory(staff).id)

    resp = client.get("/cabinet/superadmin/tags")

    assert resp.status_code == 200
    assert "Махметов" not in resp.text
    assert db.query(UserTag).filter(UserTag.user_id == service_student.id).count() == 0


def test_superadmin_users_list_marks_service_account(client, user_factory, session_factory, service_student):
    staff, _ = _staff(user_factory)
    client.cookies.set("session_id", session_factory(staff).id)

    resp = client.get("/cabinet/superadmin/users")

    assert resp.status_code == 200
    assert "Махметов" in resp.text
    assert "служебный" in resp.text
