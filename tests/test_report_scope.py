"""Кого считать в статистике — одно правило (`services/report_scope.py`).

Владелец 07.10.2026: «не учитывать заблокированных, удалённых, только с
активной подпиской». До этого сервисы статистики отбирали учеников каждый
по-своему, и числа на страницах расходились.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.activity_stats import get_login_stats, get_curator_review_speed
from app.services.report_scope import reportable_student_id_set, reportable_students_q
from app.services.staff_dashboard import get_student_activity_overview


def _students(user_factory, db):
    now = datetime.now(timezone.utc)
    active = user_factory(vk_id=950_001, name="Active")
    future = user_factory(vk_id=950_002, name="Paid till next week")
    future.access_until = now + timedelta(days=7)
    expired = user_factory(vk_id=950_003, name="Expired")
    expired.access_until = now - timedelta(minutes=1)
    blocked = user_factory(vk_id=950_004, name="Blocked", is_active=False)
    archived = user_factory(vk_id=950_005, name="Archived")
    archived.archived_at = now - timedelta(days=1)
    deleted = user_factory(vk_id=950_006, name="Deleted")
    deleted.deleted_at = now - timedelta(days=1)
    curator = user_factory(vk_id=950_007, name="Curator", role_name="куратор")
    db.commit()
    return {
        "in": {active.id, future.id},
        "out": {expired.id, blocked.id, archived.id, deleted.id, curator.id},
        "active": active,
        "expired": expired,
        "curator": curator,
    }


def test_only_active_unblocked_undeleted_students_with_live_subscription(db, user_factory):
    people = _students(user_factory, db)

    assert reportable_student_id_set(db) == people["in"]
    assert {u.id for u in reportable_students_q(db).all()} == people["in"]


def test_service_account_is_out(db, user_factory):
    from app.constants import REPORT_EXCLUDED_USER_IDS

    service_id = min(REPORT_EXCLUDED_USER_IDS)
    service = User(id=service_id, vk_id=950_099, name="Служебный", tariff="")
    service.role_id = user_factory(vk_id=950_098).role_id
    db.add(service)
    db.commit()

    assert service_id not in reportable_student_id_set(db)


def test_pages_count_the_same_students(db, user_factory):
    """«Статистика активности» и «Ученики поимённо» видят одних и тех же."""
    people = _students(user_factory, db)

    assert get_login_stats(db)["total"] == len(people["in"])
    overview_ids = {row["id"] for row in get_student_activity_overview(db)["students"]}
    assert overview_ids == people["in"]


def test_history_of_a_gone_student_leaves_the_metrics(db, user_factory):
    """Работы ученика с истёкшей подпиской уходят и из прошлых метрик
    (решение владельца 07.10.2026)."""
    people = _students(user_factory, db)
    now = datetime.now(timezone.utc)
    for student, score in ((people["active"], "60"), (people["expired"], "90")):
        db.add(Work(
            user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="октябрь",
            year=now.year, filename=f"{student.id}.jpg", status="success",
            created_at=now - timedelta(hours=3), scored_at=now - timedelta(hours=1),
            scored_by_id=people["curator"].id, score=Decimal(score),
        ))
    db.commit()

    rows = get_curator_review_speed(db)

    assert [row["scored_count"] for row in rows] == [1]
