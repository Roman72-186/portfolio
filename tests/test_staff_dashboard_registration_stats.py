from datetime import timedelta

from app.constants import (
    TARIFF_CONFIDENT_MAX,
    TARIFF_SELF,
    TARIFF_WITH_YOU,
)
from app.models.role import Role
from app.models.user import User
from app.services.staff_dashboard import (
    REGISTRATION_STATS_SINCE,
    build_tariff_registration_csv,
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
