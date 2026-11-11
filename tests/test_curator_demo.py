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


def _submission(db, student, block, count, submitted_at=None):
    submission = TaskBlockSubmission(block_id=block.id, user_id=student.id, submitted_at=submitted_at)
    db.add(submission)
    db.flush()
    for index in range(count):
        db.add(TaskBlockSubmissionImage(submission_id=submission.id, image_s3_url=f"https://example.com/{index}.jpg"))


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
    dashboard = client.get("/cabinet/curator")
    assert 'href="/cabinet/curator/demo"' in dashboard.text
    response = client.get("/cabinet/curator/demo", params={"stage_id": stage.id, "cycle_id": cycle.id})
    assert response.status_code == 200
    assert "Ученик своей группы" in response.text
    assert "Ученик чужой группы" not in response.text
    assert "private" in response.headers["cache-control"]
    assert_static_versioned(response.text)

    db.add(SectionAccessRule(section_key="statistics", user_id=curator.id, level="none"))
    db.commit()
    assert client.get("/cabinet/curator/demo").status_code == 403
    assert 'href="/cabinet/curator/demo"' not in client.get("/cabinet/curator").text
    client.cookies.set("session_id", session_factory(own).id)
    assert client.get("/cabinet/curator/demo").status_code == 403
