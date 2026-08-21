"""Сервисы учебной программы: календарь, служебные темы, билеты."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.constants import FEATURE_MOCK_EXAM
from app.models.exam_assignment import (
    ExamAssignment,
    ExamTicketAssignee,
    ExamTicketTag,
)
from app.models.feature_period import FeaturePeriod
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, TOPIC_KIND_WEEK, LearningTopic
from app.models.tag import Tag, UserTag
from app.models.tracker import ITEM_MOCK_EXAM, ITEM_VIDEO, TrackerTask
from app.services import exam_tickets, program
from app.services.exam_cycle import get_active_tickets
from app.services.tz import MSK_TZ
from app.services.video_topics import get_topic, list_topics


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


# ── Календарь ─────────────────────────────────────────────────────────────

def test_week_start_takes_monday_of_the_week():
    assert program.week_start(date(2026, 8, 22)) == date(2026, 8, 17)   # суббота
    assert program.week_start(date(2026, 8, 23)) == date(2026, 8, 17)   # воскресенье
    assert program.week_start(date(2026, 8, 17)) == date(2026, 8, 17)   # сам понедельник


def test_evening_task_stays_in_its_moscow_day():
    """21:30 UTC — это уже следующие сутки по Москве.

    Без пересчёта вечерние элементы уезжали бы в календаре на день назад.
    """
    value = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)
    assert program.msk_date(value) == date(2026, 8, 25)


def test_month_grid_starts_on_monday_and_marks_weekends():
    days = program.month_days(2026, 2, today=date(2026, 2, 10))

    assert days[0]["iso"] == "2026-01-26"          # понедельник до 1 февраля
    assert len(days) % 7 == 0                       # сетка целыми неделями
    weekend = [d for d in days if d["is_weekend"]]
    assert all(d["dow"] in (6, 7) for d in weekend)
    assert {d["iso"] for d in days if d["is_today"]} == {"2026-02-10"}
    assert days[0]["in_month"] is False and days[0]["is_past"] is True


def test_month_shift_crosses_the_year():
    assert program.shift_month(2026, 12, 1) == (2027, 1)
    assert program.shift_month(2026, 1, -1) == (2025, 12)


def test_month_marks_group_items_by_type(db, user_factory):
    admin = user_factory(vk_id=510_001, role_name="админ")
    db.add_all(
        [
            TrackerTask(
                title="Пробник по рисунку",
                kind=ITEM_MOCK_EXAM,
                subject="Рисунок",
                due_at=datetime(2026, 8, 24, 8, 45, tzinfo=timezone.utc),
                created_by_id=admin.id,
            ),
            TrackerTask(
                title="Видео недели",
                kind=ITEM_VIDEO,
                due_at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
                created_by_id=admin.id,
            ),
        ]
    )
    db.commit()

    marks = program.month_marks(db, 2026, 8)

    assert marks["2026-08-24"]["mock"] == ["Рисунок"]
    assert marks["2026-08-26"]["video"] == 1
    assert marks["2026-08-24"]["total"] == 1


def test_tags_split_separates_tariffs(db):
    _tag(db, "МАКСИМУМ")
    _tag(db, "Поток 1")

    tariffs, others = program.tags_split(db)

    assert [t.name for t in tariffs] == ["МАКСИМУМ"]
    assert [t.name for t in others] == ["Поток 1"]


# ── Служебная тема элемента ───────────────────────────────────────────────

def test_item_topic_opens_at_the_start_of_its_week(db, user_factory):
    """Владелец решил 20.08: ученик видит неделю целиком, а не по одному дню."""
    admin = user_factory(vk_id=511_001, role_name="админ")

    topic = program.ensure_item_topic(
        db, title="Пробник", day=date(2026, 8, 26), user_id=admin.id
    )
    db.commit()

    assert topic.kind == TOPIC_KIND_PROGRAM_ITEM
    assert topic.is_published is True
    assert topic.opens_at.astimezone(MSK_TZ).date() == date(2026, 8, 24)
    assert topic.assign_to_all is False      # аудитория задаётся следующим шагом


def test_service_topics_stay_out_of_the_week_lists(db, user_factory):
    admin = user_factory(vk_id=511_002, role_name="админ")
    week = LearningTopic(
        title="Неделя 1",
        opens_at=datetime.now(timezone.utc),
        kind=TOPIC_KIND_WEEK,
    )
    db.add(week)
    db.commit()
    item_topic = program.ensure_item_topic(
        db, title="Видео", day=date(2026, 8, 26), user_id=admin.id
    )
    db.commit()

    assert [t.id for t in list_topics(db)] == [week.id]
    assert {t.id for t in list_topics(db, kinds=None)} == {week.id, item_topic.id}
    # Экран недель не должен открыться на служебной теме.
    assert get_topic(db, item_topic.id, kinds=(TOPIC_KIND_WEEK,)) is None
    assert get_topic(db, item_topic.id) is not None


def test_item_audience_counts_by_topic_tags(db, user_factory):
    admin = user_factory(vk_id=511_003, role_name="админ")
    tag = _tag(db, "УВЕРЕННЫЙ")
    student = user_factory(vk_id=511_004, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()

    topic = program.ensure_item_topic(
        db, title="Домашка", day=date(2026, 8, 26), user_id=admin.id
    )
    assert program.item_audience(db, topic) == 0     # пока никому

    program.set_item_audience(
        db, topic, assign_to_all=False, tag_ids=[tag.id], assignee_ids=[]
    )
    db.commit()

    assert program.item_audience(db, topic) == 1


# ── Билеты ────────────────────────────────────────────────────────────────

def test_default_window_is_the_chosen_day(db):
    schedule = exam_tickets.default_schedule_for_day(date(2026, 8, 24))

    assert schedule["opens_at"] == "2026-08-24T11:45"
    assert schedule["closes_at"] == "2026-08-24T18:30"
    assert schedule["duration_minutes"] == 90


def test_window_shorter_than_the_work_time_is_rejected():
    opens = datetime(2036, 8, 24, 8, 45, tzinfo=timezone.utc)
    closes = opens + timedelta(minutes=30)

    with pytest.raises(HTTPException) as error:
        exam_tickets.validate_window(
            ticket_number=1,
            opens_at=opens,
            closes_at=closes,
            duration_minutes=90,
            restrict_start_by_duration=True,
        )

    assert error.value.status_code == 422


def test_window_returns_legacy_dates_in_moscow(db):
    opens = datetime(2036, 8, 24, 8, 45, tzinfo=timezone.utc)     # 11:45 МСК
    closes = datetime(2036, 8, 24, 15, 30, tzinfo=timezone.utc)   # 18:30 МСК

    start, end = exam_tickets.validate_window(
        ticket_number=1,
        opens_at=opens,
        closes_at=closes,
        duration_minutes=90,
        restrict_start_by_duration=True,
    )

    assert start == date(2036, 8, 24) and end == date(2036, 8, 24)


def test_ticket_keeps_all_tags_and_materializes_students(db, user_factory):
    """Первый тег — планировщику, полный список — доступу, носители — рассылке."""
    admin = user_factory(vk_id=512_001, role_name="админ")
    tariff = _tag(db, "МАКСИМУМ")
    extra = _tag(db, "Поток 1")
    student = user_factory(vk_id=512_002, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=extra.id))
    db.commit()

    assignment = ExamAssignment(
        title="Пробник №1 · Рисунок",
        subject="Рисунок",
        kind="mock",
        status="published",
        created_by_id=admin.id,
    )
    db.add(assignment)
    db.flush()

    ticket = exam_tickets.create_ticket(
        db,
        assignment,
        number=1,
        title="Натюрморт",
        description=None,
        image_url=None,
        image_path=None,
        opens_at=datetime(2036, 8, 24, 8, 45, tzinfo=timezone.utc),
        closes_at=datetime(2036, 8, 24, 15, 30, tzinfo=timezone.utc),
        duration_minutes=90,
        restrict_start_by_duration=True,
        start_date=date(2036, 8, 24),
        end_date=date(2036, 8, 24),
        assign_to_all=False,
        tag_ids=[tariff.id, extra.id],
        assignee_ids=[],
    )
    db.commit()

    assert ticket.target_tag_id == tariff.id
    assert {row.tag_id for row in db.query(ExamTicketTag).all()} == {tariff.id, extra.id}
    assert ticket.assign_to_all is False
    # Носитель второго тега попал в адресаты, иначе рассылка его не найдёт.
    assignees = [row.user_id for row in db.query(ExamTicketAssignee).all()]
    assert student.id in assignees


def test_second_tag_opens_the_ticket_for_a_student(db, user_factory):
    """Доступ считается по всему списку тегов, а не только по первому."""
    admin = user_factory(vk_id=513_001, role_name="админ")
    tariff = _tag(db, "МАКСИМУМ")
    extra = _tag(db, "Поток 2")
    student = user_factory(vk_id=513_002, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=extra.id))
    db.commit()

    assignment = ExamAssignment(
        title="Пробник №1 · Рисунок",
        subject="Рисунок",
        kind="mock",
        status="published",
        created_by_id=admin.id,
    )
    db.add(assignment)
    db.flush()
    opened = datetime.now(timezone.utc) - timedelta(hours=1)
    exam_tickets.create_ticket(
        db,
        assignment,
        number=1,
        title="Натюрморт",
        description=None,
        image_url=None,
        image_path=None,
        opens_at=opened,
        closes_at=opened + timedelta(hours=6),
        duration_minutes=90,
        restrict_start_by_duration=True,
        start_date=opened.date(),
        end_date=opened.date(),
        assign_to_all=False,
        tag_ids=[tariff.id, extra.id],
        assignee_ids=[],
    )
    db.commit()

    tickets = get_active_tickets(db, student.id, "Рисунок")

    assert [t.title for t in tickets] == ["Натюрморт"]


def test_ticket_without_audience_reaches_nobody(db, user_factory):
    """Старая форма при пустой адресации молча раздавала билет всей школе."""
    admin = user_factory(vk_id=514_001, role_name="админ")
    student = user_factory(vk_id=514_002, role_name="ученик")
    assignment = ExamAssignment(
        title="Пробник №2 · Рисунок",
        subject="Рисунок",
        kind="mock",
        status="published",
        created_by_id=admin.id,
    )
    db.add(assignment)
    db.flush()
    opened = datetime.now(timezone.utc) - timedelta(hours=1)
    exam_tickets.create_ticket(
        db,
        assignment,
        number=1,
        title="Ничей билет",
        description=None,
        image_url=None,
        image_path=None,
        opens_at=opened,
        closes_at=opened + timedelta(hours=6),
        duration_minutes=90,
        restrict_start_by_duration=True,
        start_date=opened.date(),
        end_date=opened.date(),
        assign_to_all=False,
        tag_ids=[],
        assignee_ids=[],
    )
    db.commit()

    assert get_active_tickets(db, student.id, "Рисунок") == []


# ── Период сдачи ──────────────────────────────────────────────────────────

def test_future_mock_does_not_open_the_period_today(db, user_factory):
    """Период начинается в день билета: иначе сегодня открылись бы старые."""
    admin = user_factory(vk_id=515_001, role_name="админ")
    day = date(2036, 9, 7)

    exam_tickets.ensure_mock_period_for(
        db, start_date=day, end_date=day, user_id=admin.id
    )
    db.commit()

    period = db.query(FeaturePeriod).filter_by(feature=FEATURE_MOCK_EXAM).one()
    assert period.start_date == day
    assert period.end_date == day


def test_existing_period_is_extended_not_duplicated(db, user_factory):
    admin = user_factory(vk_id=515_002, role_name="админ")
    db.add(
        FeaturePeriod(
            feature=FEATURE_MOCK_EXAM,
            title="Ручной период",
            start_date=date(2036, 9, 1),
            end_date=date(2036, 9, 10),
            is_active=True,
            created_by_id=admin.id,
        )
    )
    db.commit()

    exam_tickets.ensure_mock_period_for(
        db, start_date=date(2036, 9, 7), end_date=date(2036, 9, 20), user_id=admin.id
    )
    db.commit()

    periods = db.query(FeaturePeriod).filter_by(feature=FEATURE_MOCK_EXAM).all()
    assert len(periods) == 1
    assert periods[0].end_date == date(2036, 9, 20)
