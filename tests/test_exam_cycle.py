"""Тесты сервиса services/exam_cycle.py и flow цикла."""
from datetime import date, datetime, timezone

import pytest

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.tracker import ITEM_MOCK_EXAM, SOURCE_EXAM_ASSIGNMENT, STATUS_DONE, TrackerTaskState
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.exam_cycle import (
    close_cycle,
    close_cycle_auto,
    find_latest_cycle,
    get_or_create_cycle_for_probnik,
    get_score_neighbor_candidates,
    next_attempt_number,
    pick_score_neighbors,
)
from app.services.tracker import create_task


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


def _make_stored_final_work(db, *, user_id, cycle_id, work_type, attempt, score=None):
    """Как `_make_final_work`, но проходит `_stored_work_file_filter()` (нужен
    `close_cycle`/`close_cycle_auto`, которые ищут именно сохранённый файл)."""
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
        s3_path=f"probniki/{user_id}/{cycle_id}/{attempt}.jpg",
        score=score,
    )
    db.add(w)
    db.commit()
    return w


def _make_mock_exam_ticket(db, *, user_id, subject="Рисунок"):
    assignment = ExamAssignment(
        title="Пробник 1", subject=subject, kind="mock",
        created_by_id=user_id, status="published",
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=1, title="Билет 1",
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), assign_to_all=True,
    )
    db.add(ticket)
    db.commit()
    return assignment, ticket


def test_close_cycle_auto_closes_without_score(db, regular_user):
    """Тариф без обратной связи — оценивать некому, балл не требуется."""
    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=None,
    )
    db.commit()
    _make_stored_final_work(
        db, user_id=regular_user.id, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM, attempt=1,
    )

    assert close_cycle_auto(db, cycle) is True
    assert cycle.closed_at is not None


def test_close_cycle_auto_is_idempotent(db, regular_user):
    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=None,
    )
    db.commit()
    _make_stored_final_work(
        db, user_id=regular_user.id, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM, attempt=1,
    )
    assert close_cycle_auto(db, cycle) is True
    assert close_cycle_auto(db, cycle) is False


def test_close_cycle_still_requires_score(db, regular_user):
    """close_cycle (тариф с обратной связью) не меняет своё поведение."""
    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=None,
    )
    db.commit()
    _make_stored_final_work(
        db, user_id=regular_user.id, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM, attempt=1,
    )
    assert close_cycle(db, cycle) is False


def test_close_cycle_auto_closes_related_tracker_task(db, regular_user):
    _, ticket = _make_mock_exam_ticket(db, user_id=regular_user.id)
    task = create_task(
        db, title="Пробник по предмету «Рисунок»", user_id=regular_user.id,
        kind=ITEM_MOCK_EXAM, source_kind=SOURCE_EXAM_ASSIGNMENT,
        source_id=ticket.assignment_id, subject="Рисунок", assign_to_all=True,
    )
    task.is_published = True
    db.commit()

    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=ticket.id,
    )
    db.commit()
    _make_stored_final_work(
        db, user_id=regular_user.id, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM, attempt=1,
    )

    assert close_cycle_auto(db, cycle) is True

    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task.id, TrackerTaskState.user_id == regular_user.id)
        .one()
    )
    assert state.status == STATUS_DONE
    assert state.completion_source == "auto"


def test_close_cycle_closes_related_tracker_task_with_staff_source(db, regular_user):
    _, ticket = _make_mock_exam_ticket(db, user_id=regular_user.id)
    task = create_task(
        db, title="Пробник по предмету «Рисунок»", user_id=regular_user.id,
        kind=ITEM_MOCK_EXAM, source_kind=SOURCE_EXAM_ASSIGNMENT,
        source_id=ticket.assignment_id, subject="Рисунок", assign_to_all=True,
    )
    task.is_published = True
    db.commit()

    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=ticket.id,
    )
    db.commit()
    _make_stored_final_work(
        db, user_id=regular_user.id, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM,
        attempt=1, score=85,
    )

    assert close_cycle(db, cycle) is True

    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task.id, TrackerTaskState.user_id == regular_user.id)
        .one()
    )
    assert state.status == STATUS_DONE
    assert state.completion_source == "staff"


def test_close_cycle_auto_without_ticket_id_does_not_crash(db, regular_user):
    """Легаси/гостевой цикл без ticket_id — резолвить нечего, но close всё равно проходит."""
    cycle, _ = get_or_create_cycle_for_probnik(
        db, user_id=regular_user.id, subject="Рисунок", ticket_id=None,
    )
    db.commit()
    _make_stored_final_work(
        db, user_id=regular_user.id, cycle_id=cycle.id, work_type=WORK_TYPE_MOCK_EXAM, attempt=1,
    )
    assert close_cycle_auto(db, cycle) is True
    assert cycle.closed_at is not None


# ── Подборка соседей по баллу (Портфолио → Пробные экзамены, владелец 11.09.2026) ──

def _make_ticket(db, *, subject="Рисунок", creator_id):
    assignment = ExamAssignment(
        title="Пробник", subject=subject, created_by_id=creator_id, status="published",
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=1, title="Билет",
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), assign_to_all=True,
    )
    db.add(ticket)
    db.flush()
    return ticket


def _make_scored_final(db, *, user_id, ticket_id, subject, score, work_type=WORK_TYPE_MOCK_EXAM):
    cycle = ExamCycle(user_id=user_id, subject=subject, ticket_id=ticket_id, started_at=date(2026, 1, 5))
    db.add(cycle)
    db.flush()
    w = Work(
        user_id=user_id, work_type=work_type, month="01", year=2026,
        filename=f"w-{user_id}-{work_type}.jpg", subject=subject, status="success",
        cycle_id=cycle.id, is_final=True, attempt_number=1, score=score,
    )
    db.add(w)
    db.commit()
    return cycle, w


def test_pick_score_neighbors_empty_candidates():
    assert pick_score_neighbors(70, []) == {"below": [], "above": []}


def test_pick_score_neighbors_picks_nearest_not_extremes():
    """Ближайшие 2 по обе стороны, а не абсолютный минимум/максимум пула."""
    candidates = [(50, 1), (65, 2), (68, 3), (72, 4), (75, 5), (95, 6)]
    picked = pick_score_neighbors(70, candidates)
    assert [c[0] for c in picked["below"]] == [65, 68]
    assert [c[0] for c in picked["above"]] == [72, 75]


def test_pick_score_neighbors_equal_score_goes_below():
    candidates = [(70, 1), (60, 2)]
    picked = pick_score_neighbors(70, candidates)
    assert (70, 1) in picked["below"]
    assert picked["above"] == []


def test_pick_score_neighbors_caps_at_two_and_deterministic():
    """3 кандидата с одинаковым баллом — берём только 2, порядок стабилен
    независимо от порядка кандидатов на входе (сортировка по work_id)."""
    candidates = [(70, 3), (70, 1), (70, 2)]
    picked1 = pick_score_neighbors(70, candidates)
    picked2 = pick_score_neighbors(70, list(reversed(candidates)))
    assert len(picked1["below"]) == 2
    assert picked1["below"] == picked2["below"]


def test_get_score_neighbor_candidates_excludes_own_user(db, regular_user, user_factory):
    ticket = _make_ticket(db, creator_id=regular_user.id)
    other = user_factory(vk_id=800_001, name="Other")
    _make_scored_final(db, user_id=regular_user.id, ticket_id=ticket.id, subject="Рисунок", score=70)
    _make_scored_final(db, user_id=other.id, ticket_id=ticket.id, subject="Рисунок", score=65)

    result = get_score_neighbor_candidates(
        db, exclude_user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM, ticket_ids=[ticket.id],
    )
    assert [c[0] for c in result.get(ticket.id, [])] == [65]


def test_get_score_neighbor_candidates_no_cross_ticket_leak(db, regular_user, user_factory):
    ticket_a = _make_ticket(db, creator_id=regular_user.id)
    ticket_b = _make_ticket(db, creator_id=regular_user.id)
    other = user_factory(vk_id=800_002, name="Other")
    _make_scored_final(db, user_id=other.id, ticket_id=ticket_a.id, subject="Рисунок", score=65)
    _make_scored_final(db, user_id=other.id, ticket_id=ticket_b.id, subject="Рисунок", score=99)

    result = get_score_neighbor_candidates(
        db, exclude_user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM, ticket_ids=[ticket_a.id],
    )
    assert [c[0] for c in result.get(ticket_a.id, [])] == [65]
    assert ticket_b.id not in result


def test_get_score_neighbor_candidates_excludes_retake(db, regular_user, user_factory):
    """work_type фильтруется явно: отработка в том же цикле не подмешивается
    к соседям по пробнику."""
    ticket = _make_ticket(db, creator_id=regular_user.id)
    other = user_factory(vk_id=800_003, name="Other")
    cycle, _ = _make_scored_final(db, user_id=other.id, ticket_id=ticket.id, subject="Рисунок", score=60)
    retake = Work(
        user_id=other.id, work_type=WORK_TYPE_RETAKE, month="01", year=2026,
        filename="retake.jpg", subject="Рисунок", status="success",
        cycle_id=cycle.id, is_final=True, attempt_number=1, score=90,
    )
    db.add(retake)
    db.commit()

    result = get_score_neighbor_candidates(
        db, exclude_user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM, ticket_ids=[ticket.id],
    )
    assert [c[0] for c in result.get(ticket.id, [])] == [60]
