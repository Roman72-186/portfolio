"""Тесты планировщика _run_mock_exam_expiry_check (app/services/exam_scheduler.py)."""
from datetime import date, datetime, timedelta, timezone

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.mock_exam_attempt import MockExamAttempt
from app.services.exam_scheduler import _run_mock_exam_expiry_check


def _create_ticket(db, user, *, subject="Рисунок", status="published",
                    start_offset=-1, end_offset=30, ticket_number=1):
    today = date.today()
    assignment = ExamAssignment(
        title=f"Тест {subject}", subject=subject,
        created_by_id=user.id, status=status,
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=ticket_number,
        title=f"Билет {subject} #{ticket_number}",
        start_date=today + timedelta(days=start_offset),
        end_date=today + timedelta(days=end_offset),
        assign_to_all=True,
    )
    db.add(ticket)
    db.commit()
    return ticket


def _create_attempt(db, user, ticket, *, subject="Рисунок"):
    attempt = MockExamAttempt(
        user_id=user.id,
        subject=subject,
        ticket_id=ticket.id,
        ticket_title=ticket.title,
        started_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def test_expiry_check_keeps_attempt_past_ticket_end_date(db, regular_user):
    """Билет вышел из периода доступа (end_date < today), но задание опубликовано →
    попытка больше НЕ протухает: closes_at больше не enforced (сдача разрешена
    в любой момент после получения билета, см. mock_exam_access)."""
    ticket = _create_ticket(db, regular_user, end_offset=-1)
    attempt = _create_attempt(db, regular_user, ticket)

    _run_mock_exam_expiry_check()

    db.refresh(attempt)
    assert attempt.completed_at is None
    assert attempt.expired_at is None


def test_expiry_check_expires_attempt_with_archived_assignment(db, regular_user):
    """Задание стало неопубликованным/архивным (status != "published"), хотя
    период билета ещё не закончился → открытая попытка тоже expired_at
    (та же форма, что и в исходном инциденте: архивный assignment_id=11)."""
    ticket = _create_ticket(db, regular_user, status="archived", end_offset=30)
    attempt = _create_attempt(db, regular_user, ticket)

    _run_mock_exam_expiry_check()

    db.refresh(attempt)
    assert attempt.completed_at is None
    assert attempt.expired_at is not None


def test_expiry_check_keeps_attempt_for_active_ticket(db, regular_user):
    """Билет в периоде и задание опубликовано → попытка остаётся открытой."""
    ticket = _create_ticket(db, regular_user, status="published", end_offset=30)
    attempt = _create_attempt(db, regular_user, ticket)

    _run_mock_exam_expiry_check()

    db.refresh(attempt)
    assert attempt.completed_at is None
    assert attempt.expired_at is None


def test_expiry_check_expires_orphaned_attempt_with_null_ticket(db, regular_user):
    """Билет начатой попытки удалён → FK (ondelete=SET NULL) обнулил ticket_id.
    INNER JOIN такую попытку не видит, поэтому шедулер обязан протухать её
    отдельной веткой, иначе она висит completed_at/expired_at IS NULL вечно."""
    attempt = MockExamAttempt(
        user_id=regular_user.id,
        subject="Рисунок",
        ticket_id=None,
        ticket_title="Рисунок",
        started_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    _run_mock_exam_expiry_check()

    db.refresh(attempt)
    assert attempt.completed_at is None
    assert attempt.expired_at is not None
