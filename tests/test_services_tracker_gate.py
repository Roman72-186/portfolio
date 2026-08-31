"""Гейт «блок → неделя → месяц» (app/services/tracker.py).

Решение владельца 23.08 —
plans/2026-08-23-apparchi-week-month-gate-decisions.md: блокировка одинакова
для всех тарифов, опоздавшие не получают долгов раньше их понедельника,
месяц требует закрытых недель плюс отдельно закрытого Пробника по обоим
предметам.
"""
from datetime import date, timedelta

from app.models.exam_cycle import ExamCycle
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, TOPIC_KIND_WEEK, LearningTopic
from app.services.program import day_bounds, week_start
from app.services.tracker import (
    close_task_for_user,
    create_task,
    effective_week_start,
    is_month_complete,
    is_week_complete,
)
from app.services.tz import msk_midnight, today_msk
from app.services.video_topics import set_topic_tariffs

MARCH_MONDAY_1 = date(2026, 3, 2)
MARCH_MONDAY_2 = date(2026, 3, 9)


def _week_topic(db, owner, monday: date) -> LearningTopic:
    topic = LearningTopic(
        title=f"Неделя {monday.isoformat()}",
        opens_at=msk_midnight(monday),
        assign_to_all=True,
        is_published=True,
        kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _task(db, owner, *, kind, due_on: date, is_required=True, subject=None):
    task = create_task(
        db, title=f"{kind}-{due_on.isoformat()}-{subject or ''}", user_id=owner.id,
        kind=kind, due_at=day_bounds(due_on)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=is_required, subject=subject,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


# ── is_week_complete ────────────────────────────────────────────────────────

def test_optional_element_does_not_block_week(db, regular_user):
    monday = week_start(today_msk())
    _week_topic(db, regular_user, monday)
    _task(db, regular_user, kind="video", due_on=monday, is_required=False)

    assert is_week_complete(db, regular_user.id, monday) is True


def test_required_element_blocks_week(db, regular_user):
    monday = week_start(today_msk())
    _week_topic(db, regular_user, monday)
    _task(db, regular_user, kind="video", due_on=monday, is_required=True)

    assert is_week_complete(db, regular_user.id, monday) is False


def _program_item_topic(db, owner, monday: date, *, tariffs=None) -> LearningTopic:
    """Служебная тема одного элемента (созвон 26.08.2026) — в отличие от
    `_week_topic` выше, `kind=program_item`, как заводит `ensure_item_topic`."""
    topic = LearningTopic(
        title="Элемент недели",
        opens_at=msk_midnight(monday),
        assign_to_all=True,
        is_published=True,
        kind=TOPIC_KIND_PROGRAM_ITEM,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.flush()
    set_topic_tariffs(db, topic, tariff_restricted=True, tariffs=tariffs or [])
    db.commit()
    db.refresh(topic)
    return topic


def test_tariff_hidden_required_element_does_not_block_week(db, user_factory):
    """Владелец 26.08.2026: элемент, скрытый по тарифу, не должен запирать
    неделю ученику, которому он вообще не показан — не нужен спецкод в
    is_week_complete, скрытие уже происходит на уровне accessible_task_entries."""
    monday = week_start(today_msk())
    student = user_factory(vk_id=410_001, tariff="УВЕРЕННЫЙ")
    topic = _program_item_topic(db, student, monday, tariffs=["МАКСИМУМ"])
    task = create_task(
        db, title="Видео только МАКСИМУМ", user_id=student.id,
        kind="video", due_at=day_bounds(monday)[0] + timedelta(hours=6),
        topic_id=topic.id, is_required=True,
    )
    task.is_published = True
    db.commit()

    assert is_week_complete(db, student.id, monday) is True


def test_tariff_matching_required_element_still_blocks_week(db, user_factory):
    monday = week_start(today_msk())
    student = user_factory(vk_id=410_002, tariff="МАКСИМУМ")
    topic = _program_item_topic(db, student, monday, tariffs=["МАКСИМУМ"])
    task = create_task(
        db, title="Видео только МАКСИМУМ", user_id=student.id,
        kind="video", due_at=day_bounds(monday)[0] + timedelta(hours=6),
        topic_id=topic.id, is_required=True,
    )
    task.is_published = True
    db.commit()

    assert is_week_complete(db, student.id, monday) is False


def test_both_subjects_checked_without_split(db, regular_user):
    """Рисунок закрыт, Композиция нет → неделя не пройдена целиком."""
    monday = week_start(today_msk())
    _week_topic(db, regular_user, monday)
    drawing = _task(db, regular_user, kind="homework", due_on=monday, subject="Рисунок")
    _task(db, regular_user, kind="homework", due_on=monday, subject="Композиция")
    close_task_for_user(db, drawing, regular_user.id, source="auto")
    db.commit()

    assert is_week_complete(db, regular_user.id, monday) is False


def test_unclosed_mock_exam_task_does_not_block_week(db, regular_user):
    # Билет Пробника создаётся как is_required=True (cabinet_program.py), но
    # по решению владельца 24.08 он показывается внутри вкладки «Задание» и
    # блокирует только переход на следующий месяц — не неделю. До этого теста
    # проверка не различала kind и посчитала бы открытый билет долгом недели.
    monday = week_start(today_msk())
    _week_topic(db, regular_user, monday)
    _task(db, regular_user, kind="mock_exam", due_on=monday, subject="Рисунок")

    assert is_week_complete(db, regular_user.id, monday) is True


def test_week_complete_when_both_subjects_done(db, regular_user):
    monday = week_start(today_msk())
    _week_topic(db, regular_user, monday)
    drawing = _task(db, regular_user, kind="homework", due_on=monday, subject="Рисунок")
    composition = _task(db, regular_user, kind="homework", due_on=monday, subject="Композиция")
    close_task_for_user(db, drawing, regular_user.id, source="auto")
    close_task_for_user(db, composition, regular_user.id, source="auto")
    db.commit()

    assert is_week_complete(db, regular_user.id, monday) is True


# ── effective_week_start ────────────────────────────────────────────────────

def test_effective_week_start_without_debt_is_current_week(db, regular_user):
    monday = week_start(today_msk())
    _week_topic(db, regular_user, monday)
    _task(db, regular_user, kind="video", due_on=monday, is_required=False)

    assert effective_week_start(db, regular_user.id, today_msk()) == monday


def test_effective_week_start_returns_first_incomplete_week(db, regular_user):
    """Долг прошлой недели держит ученика на ней, а не на текущей."""
    today = today_msk()
    monday = week_start(today)
    last_monday = monday - timedelta(days=7)
    # Ученик учится давно — граница по регистрации не мешает найти долг.
    regular_user.created_at = msk_midnight(last_monday - timedelta(days=30))
    db.commit()

    _week_topic(db, regular_user, last_monday)
    _task(db, regular_user, kind="video", due_on=last_monday, is_required=True)
    _week_topic(db, regular_user, monday)

    assert effective_week_start(db, regular_user.id, today) == last_monday


def test_late_student_not_pulled_before_their_monday(db, regular_user):
    """Опоздавший не тащится на неделю, которая была до его регистрации."""
    today = today_msk()
    monday = week_start(today)
    two_weeks_ago = monday - timedelta(days=14)
    regular_user.created_at = msk_midnight(monday)
    db.commit()

    _week_topic(db, regular_user, two_weeks_ago)
    _task(db, regular_user, kind="video", due_on=two_weeks_ago, is_required=True)
    _week_topic(db, regular_user, monday)
    _task(db, regular_user, kind="video", due_on=monday, is_required=False)

    assert effective_week_start(db, regular_user.id, today) == monday


# ── is_month_complete ───────────────────────────────────────────────────────

def _prep_march_weeks_done(db, owner):
    for monday in (MARCH_MONDAY_1, MARCH_MONDAY_2):
        _week_topic(db, owner, monday)
        _task(db, owner, kind="video", due_on=monday, is_required=False)


def test_unclosed_mock_exam_blocks_month(db, regular_user):
    _prep_march_weeks_done(db, regular_user)
    db.add(ExamCycle(
        user_id=regular_user.id, subject="Рисунок",
        started_at=MARCH_MONDAY_1, closed_at=None,
    ))
    db.add(ExamCycle(
        user_id=regular_user.id, subject="Композиция",
        started_at=MARCH_MONDAY_1, closed_at=None,
    ))
    db.commit()

    assert is_month_complete(db, regular_user.id, 2026, 3) is False


def test_missing_mock_exam_blocks_month(db, regular_user):
    """По предмету нет ни одного цикла в этом месяце — месяц не закрыт."""
    _prep_march_weeks_done(db, regular_user)

    assert is_month_complete(db, regular_user.id, 2026, 3) is False


def test_month_complete_when_weeks_and_both_mocks_closed(db, regular_user):
    _prep_march_weeks_done(db, regular_user)
    from datetime import datetime, timezone
    db.add(ExamCycle(
        user_id=regular_user.id, subject="Рисунок",
        started_at=MARCH_MONDAY_1, closed_at=datetime.now(timezone.utc),
    ))
    db.add(ExamCycle(
        user_id=regular_user.id, subject="Композиция",
        started_at=MARCH_MONDAY_1, closed_at=datetime.now(timezone.utc),
    ))
    db.commit()

    assert is_month_complete(db, regular_user.id, 2026, 3) is True
