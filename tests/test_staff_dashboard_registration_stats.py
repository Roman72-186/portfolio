from datetime import datetime, timedelta, timezone

from app.constants import (
    TARIFF_CONFIDENT_MAX,
    TARIFF_SELF,
    TARIFF_WITH_YOU,
)
from app.models.role import Role
from app.models.session import Session
from app.models.user import User
from app.models.work import Work, WORK_TYPE_AFTER, WORK_TYPE_BEFORE
from app.services.staff_dashboard import (
    REGISTRATION_STATS_SINCE,
    build_tariff_registration_csv,
    get_student_activity_overview,
    get_tariff_registration_stats,
)
from app.services.tz import msk_midnight
def test_tariff_registration_stats_use_tracking_window_and_student_role(
    db, user_factory
):
    cutoff = msk_midnight(REGISTRATION_STATS_SINCE)

    self_student = user_factory(vk_id=810_001, tariff=TARIFF_SELF)
    self_student.tg_username = "self_student"
    with_you_student = user_factory(vk_id=810_002, tariff=TARIFF_WITH_YOU)
    max_student = user_factory(vk_id=810_003, tariff=TARIFF_CONFIDENT_MAX)
    no_tariff_student = user_factory(vk_id=810_004, tariff="")
    inactive_student = user_factory(
        vk_id=810_005, tariff=TARIFF_WITH_YOU, is_active=False
    )
    archived_student = user_factory(vk_id=810_006, tariff=TARIFF_SELF)
    archived_student.archived_at = cutoff
    staff = user_factory(
        vk_id=810_007, tariff=TARIFF_CONFIDENT_MAX, role_name="админ"
    )
    old_student = user_factory(vk_id=810_008, tariff=TARIFF_CONFIDENT_MAX)
    legacy_student = user_factory(vk_id=810_009, tariff="УВЕРЕННЫЙ")
    student_role = db.query(Role).filter(Role.rank == 1).one()
    owner = User(
        id=199,
        vk_id=810_199,
        name="Роман Архитектор ЧАТ-БОТОВ 2.0",
        tariff="",
        role_id=student_role.id,
        created_at=cutoff,
    )
    db.add(owner)

    for user in (
        self_student,
        with_you_student,
        max_student,
        no_tariff_student,
        inactive_student,
        archived_student,
        staff,
        legacy_student,
    ):
        user.created_at = cutoff
    old_student.created_at = cutoff - timedelta(seconds=1)
    db.commit()

    stats = get_tariff_registration_stats(db)

    assert stats["since_label"] == "18.09.2026"
    assert {item["tariff"]: item["count"] for item in stats["by_tariff"]} == {
        TARIFF_SELF: 2,
        TARIFF_WITH_YOU: 2,
        TARIFF_CONFIDENT_MAX: 1,
    }
    assert stats["without_tariff"] == 1
    assert stats["total"] == 6
    assert len(stats["students"]) == 7  # шесть в сводке + legacy для проверки
    tariff_labels = [student["tariff_label"] for student in stats["students"]]
    assert tariff_labels == sorted(tariff_labels, key=str.casefold)
    assert stats["students"][-1]["username"] == "@self_student"
    assert all(student["id"] != 199 for student in stats["students"])


def test_tariff_registration_stats_return_zero_buckets(db):
    stats = get_tariff_registration_stats(db)

    assert [item["count"] for item in stats["by_tariff"]] == [0, 0, 0]
    assert stats["without_tariff"] == 0
    assert stats["total"] == 0
    assert stats["students"] == []


def test_tariff_registration_csv_contains_visible_students_and_headers(db, user_factory):
    student = user_factory(vk_id=810_100, tariff=TARIFF_SELF, name="CSV Student")
    student.tg_username = "csv_student"
    db.commit()

    csv_text = build_tariff_registration_csv(get_tariff_registration_stats(db))

    assert csv_text.startswith("\ufeffИмя;Username;Тариф;Дата регистрации\r\n")
    assert "CSV Student;@csv_student;" in csv_text


def test_student_activity_overview_tracks_logins_and_portfolio_uploads(db, user_factory):
    student = user_factory(vk_id=810_101, tariff=TARIFF_SELF, name="Activity Student")
    first_login = datetime(2026, 9, 18, 8, tzinfo=timezone.utc)
    last_login = datetime(2026, 9, 19, 8, tzinfo=timezone.utc)
    db.add_all([
        Session(user_id=student.id, expires_at=last_login),
        Session(user_id=student.id, expires_at=last_login),
        Work(
            user_id=student.id,
            work_type=WORK_TYPE_BEFORE,
            month="Сентябрь",
            year=2026,
            filename="portfolio.jpg",
            status="success",
            created_at=last_login,
        ),
    ])
    db.flush()
    sessions = db.query(Session).filter(Session.user_id == student.id).all()
    sessions[0].created_at = first_login
    sessions[1].created_at = last_login
    db.commit()

    overview = get_student_activity_overview(db)
    row = next(item for item in overview["students"] if item["id"] == student.id)

    assert row["login_count"] == 2
    assert row["first_login"].replace(tzinfo=timezone.utc) == first_login
    assert row["last_login"].replace(tzinfo=timezone.utc) == last_login
    assert row["upload_count"] == 1
    assert row["has_portfolio_before"] is True
    assert row["last_upload"].replace(tzinfo=timezone.utc) == last_login


def test_student_activity_portfolio_flag_uses_successful_before_work_and_active_students(
    db, user_factory
):
    after_only = user_factory(vk_id=810_102, name="After Only")
    failed_before = user_factory(vk_id=810_103, name="Failed Before")
    inactive = user_factory(vk_id=810_104, name="Inactive", is_active=False)
    db.add_all([
        Work(
            user_id=after_only.id,
            work_type=WORK_TYPE_AFTER,
            month="Сентябрь",
            year=2026,
            filename="after.jpg",
            status="success",
        ),
        Work(
            user_id=failed_before.id,
            work_type=WORK_TYPE_BEFORE,
            month="Сентябрь",
            year=2026,
            filename="failed.jpg",
            status="failed",
        ),
    ])
    db.commit()

    rows = {item["id"]: item for item in get_student_activity_overview(db)["students"]}

    assert rows[after_only.id]["has_portfolio_before"] is False
    assert rows[failed_before.id]["has_portfolio_before"] is False
    assert inactive.id not in rows
