from datetime import datetime, timedelta, timezone

from app.constants import (
    TARIFF_CONFIDENT_MAX,
    TARIFF_SELF,
    TARIFF_WITH_YOU,
)
from app.models.role import Role
from app.models.learning_topic import LearningTopic
from app.models.task_block import BLOCK_TIMED, TaskBlock, TaskBlockState, TaskBlockSubmission, TaskBlockTariff
from app.models.tracker import TrackerTask
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

    assert stats["since_label"] == "19.09.2026"  # начало учёта сдвинуто на 19.09 коммитом e2785d7
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


def test_student_activity_assignment_counts_submitted_work_and_eligible_students(db, user_factory):
    submitted = user_factory(vk_id=810_210, name="Submitted", tariff=TARIFF_SELF)
    missing = user_factory(vk_id=810_211, name="Missing", tariff=TARIFF_SELF)
    other_tariff = user_factory(vk_id=810_212, name="Other", tariff=TARIFF_WITH_YOU)
    topic = LearningTopic(title="Предобучение", kind="week", opens_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                          is_published=True, assign_to_all=True)
    db.add(topic)
    db.flush()
    task = TrackerTask(title="Архитектурное эскизирование", topic_id=topic.id, kind="material", is_published=True)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type="photo_upload", title="Эскизы")
    db.add(block)
    db.flush()
    db.add(TaskBlockTariff(block_id=block.id, tariff=TARIFF_SELF))
    db.add(TaskBlockSubmission(block_id=block.id, user_id=submitted.id,
                               submitted_at=datetime(2026, 9, 20, tzinfo=timezone.utc), needs_revision=True))
    db.add(TaskBlockState(block_id=block.id, user_id=submitted.id, status="done",
                          completed_at=datetime(2026, 9, 20, tzinfo=timezone.utc)))
    db.commit()

    assignments = get_student_activity_overview(db, include_assignments=True)["assignments"]

    assert len(assignments) == 1
    assert assignments[0]["label"] == "Архитектурное эскизирование · Эскизы"
    assert set(assignments[0]["eligible"]) == {submitted.id, missing.id}
    assert assignments[0]["submitted"] == [submitted.id]
    assert other_tariff.id not in assignments[0]["eligible"]


# ── «Сдали / не сдали» по правилу напоминаний (владелец 03.10.2026) ─────────

def _control(db, *, submit_until=None, subject="Рисунок"):
    topic = LearningTopic(title="Цикл", kind="week", opens_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                          is_published=True, assign_to_all=True)
    db.add(topic)
    db.flush()
    task = TrackerTask(title="Контрольная по рисунку", topic_id=topic.id, kind="material",
                       is_published=True, submit_until=submit_until, subject=subject)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_TIMED, title="Локация с дверью",
                      time_limit_minutes=75)
    db.add(block)
    db.commit()
    return block


def test_student_with_expired_access_is_not_counted_as_debtor(db, user_factory):
    """Пробник, у которого доступ кончился, не должник: бот ему о сроке не
    напоминает (`tracker.program_students`), и статистика не числит. На проде
    03.10.2026 таких было шестеро в каждой контрольной."""
    active = user_factory(vk_id=810_300, name="Active")
    expired = user_factory(vk_id=810_301, name="Expired")
    expired.access_until = datetime.now(timezone.utc) - timedelta(days=6)
    not_member = user_factory(vk_id=810_302, name="No group", is_group_member=False)
    block = _control(db)

    assignment = get_student_activity_overview(db, include_assignments=True)["assignments"][0]

    assert assignment["id"] == block.id
    assert assignment["eligible"] == [active.id]
    assert expired.id not in assignment["eligible"]
    assert not_member.id not in assignment["eligible"]


def test_missing_students_are_split_by_deadline(db, user_factory):
    """«Отдельно показывать „срок прошёл“ и „срок ещё не наступил“» — срок
    у каждого свой (`upload_deadline`), строка тарифа главнее задания."""
    done = user_factory(vk_id=810_310, name="Done", tariff=TARIFF_SELF)
    overdue = user_factory(vk_id=810_311, name="Overdue", tariff=TARIFF_SELF)
    pending = user_factory(vk_id=810_312, name="Pending", tariff=TARIFF_WITH_YOU)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    block = _control(db, submit_until=past)
    from app.models.tracker import TrackerTaskTariffDeadline
    db.add(TrackerTaskTariffDeadline(task_id=block.task_id, tariff=TARIFF_WITH_YOU,
                                     submit_until=datetime.now(timezone.utc) + timedelta(days=3)))
    db.add(TaskBlockState(block_id=block.id, user_id=done.id, status="done",
                          started_at=past - timedelta(hours=2), completed_at=past - timedelta(hours=1)))
    db.commit()

    assignment = get_student_activity_overview(db, include_assignments=True)["assignments"][0]

    assert assignment["label"] == "Рисунок: Контрольная по рисунку · Локация с дверью"
    assert assignment["submitted"] == [done.id]
    assert assignment["overdue"] == [overdue.id]
    assert assignment["pending"] == [pending.id]
    assert assignment["notes"][done.id] == "Сдал"
    assert assignment["notes"][overdue.id].startswith("Не сдал · срок прошёл ")
    assert assignment["notes"][pending.id].startswith("Не сдал · срок до ")


def test_open_started_timed_block_is_not_submitted(db, user_factory):
    """Начал контрольную, но работы нет — не сдал: «сдал» решает статус
    «сделано», а не строка состояния."""
    started = user_factory(vk_id=810_320, name="Started")
    block = _control(db)
    db.add(TaskBlockState(block_id=block.id, user_id=started.id, status="open",
                          started_at=datetime.now(timezone.utc) - timedelta(hours=3)))
    db.commit()

    assignment = get_student_activity_overview(db, include_assignments=True)["assignments"][0]

    assert assignment["submitted"] == []
    assert assignment["pending"] == [started.id]
    assert assignment["notes"][started.id] == "Не сдал · срок не задан"
