from datetime import datetime, timedelta, timezone

import pytest

from app.constants import TARIFF_CONFIDENT_MAX
from app.models.learning_topic import LearningTopic, TOPIC_KIND_STAGE
from app.models.section_access import SectionAccessRule
from app.models.task_block import (
    BLOCK_TIMED, BLOCK_UPLOAD, TaskBlock, TaskBlockRequiredTariff, TaskBlockState, TaskBlockSubmission,
    TaskBlockSubmissionImage,
)
from app.services.curator_demo import build_curator_demo
from app.services.tracker import create_task
from app.services.tz import MSK_TZ
from app.services.user_management import assign_curator


@pytest.fixture
def demo_group(db, user_factory):
    now = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    curator = user_factory(vk_id=70101, role_name="куратор")
    stage = LearningTopic(
        title="Учебный месяц", kind=TOPIC_KIND_STAGE, opens_at=now - timedelta(days=5), is_published=True,
    )
    db.add(stage)
    db.flush()
    cycle = LearningTopic(
        title="Текущая учебная неделя", opens_at=now - timedelta(days=4),
        ends_at=now + timedelta(days=4), assign_to_all=True, is_published=True, parent_id=stage.id,
    )
    db.add(cycle)
    db.flush()
    task = create_task(db, user_id=curator.id, title="Практика", topic_id=cycle.id, subject="Рисунок")
    task.is_published = True
    first = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Работа 1", is_required=True, required_photos=2)
    second = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Работа 2", is_required=True)
    extra = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Дополнительная работа", is_required=False)
    db.add_all([first, second, extra])
    db.commit()
    return now, curator, stage, cycle, first, second, extra


def _student(db, user_factory, curator, vk_id, name):
    student = user_factory(vk_id=vk_id, name=name, tariff=TARIFF_CONFIDENT_MAX)
    student.curator_id = curator.id
    db.flush()
    return student


def _submission(db, student, block, count, submitted_at=None, *, score=None):
    submission = TaskBlockSubmission(block_id=block.id, user_id=student.id, submitted_at=submitted_at, score=score)
    db.add(submission)
    db.flush()
    for index in range(count):
        db.add(TaskBlockSubmissionImage(submission_id=submission.id, image_s3_url=f"https://example.com/{index}.jpg"))


def test_curator_assignment_date_changes_only_when_curator_changes(db, user_factory, demo_group):
    now, curator, *_ = demo_group
    other = user_factory(vk_id=70102, role_name="куратор")
    student = user_factory(vk_id=70103, tariff=TARIFF_CONFIDENT_MAX)

    assign_curator(db, curator.id, student, curator)
    assert student.curator_assigned_at is not None
    student.curator_assigned_at = now - timedelta(days=2)
    assign_curator(db, curator.id, student, curator)
    assert student.curator_assigned_at == now - timedelta(days=2)
    assign_curator(db, curator.id, student, other)
    assert student.curator_id == other.id
    assert student.curator_assigned_at > now - timedelta(days=2)
    assign_curator(db, curator.id, student, None)
    assert student.curator_id is None
    assert student.curator_assigned_at is None


def test_demo_recent_assignments_use_seven_day_window(db, user_factory, demo_group):
    now, curator, _, cycle, *_ = demo_group
    boundary = _student(db, user_factory, curator, 70102, "Граница")
    boundary.curator_assigned_at = now - timedelta(days=7)
    old = _student(db, user_factory, curator, 70103, "Старое")
    old.curator_assigned_at = boundary.curator_assigned_at - timedelta(seconds=1)
    _student(db, user_factory, curator, 70104, "Дата неизвестна")
    future = _student(db, user_factory, curator, 70105, "Будущее")
    future.curator_assigned_at = now + timedelta(seconds=1)
    db.commit()

    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)
    assert [row["id"] for row in overview["recent_assignments"]] == [boundary.id]


def test_demo_leaves_control_out_until_its_container_opens(db, user_factory, demo_group):
    now, curator, _, cycle, first, *_ = demo_group
    first.block_type = BLOCK_TIMED
    opens_at = now + timedelta(days=1)
    first.container_path = [{"key": "control", "opens_at": opens_at.astimezone(MSK_TZ).strftime("%Y-%m-%dT%H:%M")}]
    _student(db, user_factory, curator, 70102, "Ученик")
    db.commit()

    assert not build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)["control_options"]
    assert first.id in build_curator_demo(db, curator.id, cycle_id=cycle.id, now=opens_at)["control_options"]


def test_demo_selects_control_and_distinguishes_ungraded_from_zero(
    client, db, user_factory, session_factory, demo_group,
):
    now, curator, stage, cycle, first, second, _ = demo_group
    first.block_type = second.block_type = BLOCK_TIMED
    zero = _student(db, user_factory, curator, 70102, "Ноль")
    ungraded = _student(db, user_factory, curator, 70103, "Без оценки")
    missing = _student(db, user_factory, curator, 70104, "Не сдал")
    _submission(db, zero, second, 1, now, score=0)
    _submission(db, ungraded, second, 1, now)
    db.commit()

    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now, control_id=second.id)
    assert list(overview["control_options"]) == [first.id, second.id]
    assert overview["control_id"] == second.id
    assert {label: [row["id"] for row in rows] for label, rows in overview["control"]["groups"].items()} == {
        "0 б.": [zero.id], "Сдали без оценки": [ungraded.id], "Не сдали": [missing.id],
    }
    fallback = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now, control_id=999999)
    assert fallback["control_id"] == first.id
    client.cookies.set("session_id", session_factory(curator).id)
    response = client.get("/cabinet/curator/overview", params={
        "stage_id": stage.id, "cycle_id": cycle.id, "subject": "Рисунок", "control_id": second.id,
    })
    assert response.status_code == 200
    assert 'name="control_id"' in response.text
    assert f'<option value="{second.id}" selected>' in response.text
    for label in ("0 б.", "Сдали без оценки", "Не сдали"):
        assert f"{label} · 1" in response.text


def test_demo_counts_full_submissions_and_waiting_blocks(db, user_factory, demo_group):
    now, curator, _, cycle, first, second, extra = demo_group
    partial = _student(db, user_factory, curator, 70102, "Часть файлов")
    done = _student(db, user_factory, curator, 70103, "Сдал обязательные")
    empty = _student(db, user_factory, curator, 70104, "Пустая сдача")
    _submission(db, partial, first, 1)
    _submission(db, done, first, 2, now)
    _submission(db, done, second, 1, now)
    _submission(db, empty, first, 0)
    db.commit()

    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)
    groups = overview["weekly"][TARIFF_CONFIDENT_MAX]
    assert [row["id"] for row in groups["done"]] == [done.id]
    assert {row["id"] for row in groups["remaining"]} == {partial.id, empty.id}
    assert all(row["total"] == 2 for row in groups["remaining"])
    assert {row["id"] for row in overview["practice"]["started"]} == {done.id, partial.id}
    assert [row["id"] for row in overview["practice"]["none"]] == [empty.id]

    _submission(db, done, extra, 1, now)
    db.commit()
    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)
    assert [row["id"] for row in overview["practice"]["done"]] == [done.id]


def test_demo_respects_hidden_parent_and_calendar_days(db, user_factory, demo_group):
    now, curator, stage, cycle, first, _, _ = demo_group
    absent = _student(db, user_factory, curator, 70102, "Три дня")
    recent = _student(db, user_factory, curator, 70103, "Два дня")
    absent.last_seen_at = now - timedelta(days=4) + timedelta(hours=6)
    recent.last_seen_at = now - timedelta(days=3, hours=4)
    db.commit()
    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)
    assert overview["cycle"].id == cycle.id
    assert [row["id"] for row in overview["absent"]] == [absent.id]

    stage.is_published = False
    db.commit()
    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)
    assert overview["cycle"] is None
    assert not overview["practice_total"]


def test_demo_counts_timer_start_but_not_ordinary_block_state(db, user_factory, demo_group):
    now, curator, _, cycle, first, second, _ = demo_group
    timer_student = _student(db, user_factory, curator, 70102, "Начал контрольную")
    ordinary_student = _student(db, user_factory, curator, 70103, "Нет действий")
    second.block_type = BLOCK_TIMED
    second.is_required = False  # Контрольная блокирует выдачу даже в старых данных.
    db.add_all([
        TaskBlockState(block_id=second.id, user_id=timer_student.id, started_at=now),
        TaskBlockState(block_id=first.id, user_id=ordinary_student.id, started_at=now),
    ])
    db.commit()
    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)
    assert [row["id"] for row in overview["practice"]["started"]] == [timer_student.id]
    assert [row["id"] for row in overview["practice"]["none"]] == [ordinary_student.id]
    assert all(row["total"] == 2 for row in overview["weekly"][TARIFF_CONFIDENT_MAX]["remaining"])


def test_demo_distinguishes_practice_from_tariff_specific_week_requirements(db, user_factory, demo_group):
    from app.constants import TARIFF_WITH_YOU

    now, curator, _, cycle, first, second, _ = demo_group
    student = _student(db, user_factory, curator, 70102, "Максимум")
    db.add(TaskBlockRequiredTariff(block_id=second.id, tariff=TARIFF_WITH_YOU))
    _submission(db, student, first, 2, now)
    db.commit()

    overview = build_curator_demo(db, curator.id, cycle_id=cycle.id, now=now)
    week, = overview["weekly"][TARIFF_CONFIDENT_MAX]["done"]
    assert week["id"] == student.id
    assert week["total"] == 1
    practice, = overview["practice"]["started"]
    assert practice["remaining"] == ["Практика · Работа 2", "Практика · Дополнительная работа"]


def test_demo_route_scope_and_section_guard(client, db, user_factory, session_factory, demo_group, assert_static_versioned):
    _, curator, stage, cycle, _, _, _ = demo_group
    own = _student(db, user_factory, curator, 70102, "Ученик своей группы")
    other = user_factory(vk_id=70103, role_name="куратор")
    _student(db, user_factory, other, 70104, "Ученик чужой группы")
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)
    home = client.get("/cabinet/curator", follow_redirects=False)
    assert home.headers["location"] == "/cabinet/curator/overview"
    dashboard = client.get("/cabinet/curator?sections=1")
    assert 'href="/cabinet/curator/overview"' in dashboard.text
    assert 'href="/cabinet/curator/statistics"' in dashboard.text
    params = {"stage_id": stage.id, "cycle_id": cycle.id}
    old = client.get("/cabinet/curator/demo", params=params, follow_redirects=False)
    assert old.headers["location"] == f"/cabinet/curator/overview?stage_id={stage.id}&cycle_id={cycle.id}"
    response = client.get("/cabinet/curator/overview", params=params)
    assert response.status_code == 200
    assert "Ученик своей группы" in response.text
    assert "Ученик чужой группы" not in response.text
    assert "Демо-версия" not in response.text
    assert "private" in response.headers["cache-control"]
    assert_static_versioned(response.text)

    db.add(SectionAccessRule(section_key="statistics", user_id=curator.id, level="none"))
    db.commit()
    assert client.get("/cabinet/curator/demo").status_code == 403
    assert client.get("/cabinet/curator/overview").status_code == 403
    fallback = client.get("/cabinet/curator", follow_redirects=False)
    assert fallback.status_code == 200
    assert 'href="/cabinet/curator/overview"' not in fallback.text
    client.cookies.set("session_id", session_factory(own).id)
    assert client.get("/cabinet/curator/demo").status_code == 403


def test_review_queue_uses_assignment_week_and_selected_curator(client, db, user_factory, session_factory, demo_group, rendered):
    now, curator, _, cycle, first, _, _ = demo_group
    own = _student(db, user_factory, curator, 70102, "Работа своей группы")
    other = user_factory(vk_id=70103, role_name="куратор")
    foreign = _student(db, user_factory, other, 70104, "Работа чужой группы")
    first.block_type = BLOCK_TIMED
    _submission(db, own, first, 2, now + timedelta(days=12))
    _submission(db, foreign, first, 2, now)
    db.commit()
    chief = user_factory(role_name="админ")
    client.cookies.set("session_id", session_factory(chief).id)

    url = "/cabinet/staff/students-review/queue"
    response = client.get(url, params={"subject": "Рисунок", "cycle_id": cycle.id, "review_curator": curator.id})
    assert response.status_code == 200
    assert [card["student"].id for card in rendered("curator_review_queue.html")["cards"]] == [own.id]
