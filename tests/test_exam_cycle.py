"""Тесты сервиса services/exam_cycle.py и flow цикла."""
from datetime import datetime, timezone

import pytest

from app.models.exam_cycle import ExamCycle
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.exam_cycle import (
    find_latest_cycle,
    get_or_create_cycle_for_probnik,
    next_attempt_number,
)


def _make_final_work(db, *, user_id, cycle_id, work_type, attempt):
    w = Work(
        user_id=user_id,
        work_type=work_type,
        month="январь",
        year=2026,
        filename=f"f{attempt}.jpg",
        status="success",
        cycle_id=cycle_id,
        is_final=True,
        attempt_number=attempt,
        drive_status="s3_only",
    )
    db.add(w)
    db.commit()
    return w


def test_create_cycle_on_first_probnik(db, regular_user):
    cycle, created = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=42,
    )
    db.commit()
    assert created is True
    assert cycle.subject == "Рисунок"
    assert cycle.ticket_id == 42


def test_reuse_cycle_for_same_ticket(db, regular_user):
    """Повторная попытка по тому же билету — тот же цикл."""
    c1, created1 = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=42,
    )
    db.commit()
    c2, created2 = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=42,
    )
    db.commit()
    assert created1 is True
    assert created2 is False
    assert c1.id == c2.id


def test_new_cycle_for_new_ticket(db, regular_user):
    c1, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=42,
    )
    db.commit()
    c2, created2 = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=99,
    )
    db.commit()
    assert created2 is True
    assert c1.id != c2.id


def test_attempt_number_increment_for_mock_exam(db, regular_user):
    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=42,
    )
    db.commit()
    assert next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM) == 1
    _make_final_work(db, user_id=regular_user.id, cycle_id=cycle.id,
                     work_type=WORK_TYPE_MOCK_EXAM, attempt=1)
    assert next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM) == 2
    _make_final_work(db, user_id=regular_user.id, cycle_id=cycle.id,
                     work_type=WORK_TYPE_MOCK_EXAM, attempt=2)
    assert next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM) == 3


def test_attempt_number_separate_for_retake(db, regular_user):
    """mock_exam и retake считаются раздельно в одном цикле."""
    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Композиция", ticket_id=7,
    )
    db.commit()
    _make_final_work(db, user_id=regular_user.id, cycle_id=cycle.id,
                     work_type=WORK_TYPE_MOCK_EXAM, attempt=1)
    _make_final_work(db, user_id=regular_user.id, cycle_id=cycle.id,
                     work_type=WORK_TYPE_RETAKE, attempt=1)
    _make_final_work(db, user_id=regular_user.id, cycle_id=cycle.id,
                     work_type=WORK_TYPE_RETAKE, attempt=2)
    assert next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM) == 2
    assert next_attempt_number(db, cycle_id=cycle.id, work_type=WORK_TYPE_RETAKE) == 3


def test_find_latest_cycle_picks_newest(db, regular_user):
    c1, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=1,
    )
    db.commit()
    c2, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=2,
    )
    db.commit()
    latest = find_latest_cycle(db, regular_user.id, "Рисунок")
    assert latest is not None
    assert latest.id == c2.id


def test_find_latest_cycle_by_subject(db, regular_user):
    """Цикл по другому предмету не возвращается."""
    get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=1,
    )
    db.commit()
    assert find_latest_cycle(db, regular_user.id, "Композиция") is None
