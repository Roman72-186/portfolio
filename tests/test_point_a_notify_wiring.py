"""Точка А: последний балл, поставленный через любой из четырёх экранов,
даёт ученику уведомление об уровне и отправляет его в Telegram.

Пересчёт уровня — `test_point_a_notify.py`. Здесь — сквозной путь: балл
через настоящий роут → точка А разобрана → уведомление в базе → отправка.
Подменён только Telegram. До 20.10.2026 вместо этого стоял шпион на
`maybe_notify_point_a_level` и проверял число его вызовов.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.notification import Notification
from app.models.work import WORK_TYPE_AFTER, WORK_TYPE_BEFORE, WORK_TYPE_MOCK_EXAM, Work
from app.services import notify as notify_module

CHAT_ID = 863_777


@pytest.fixture()
def admin(user_factory):
    return user_factory(vk_id=863_001, name="ГП", role_name="админ")


@pytest.fixture()
def student(db, user_factory):
    student = user_factory(vk_id=863_002, name="Ученик")
    student.telegram_chat_id = CHAT_ID
    student.telegram_notifications_enabled = True
    db.commit()
    return student


@pytest.fixture()
def telegram(monkeypatch):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(notify_module.telegram_service, "send_message", send)
    monkeypatch.setattr(notify_module.settings, "vapid_private_key", "")
    return send


def _work(db, student, work_type, **extra):
    work = Work(
        user_id=student.id, work_type=work_type, month="сентябрь", year=2026,
        filename="a.jpg", s3_url="https://s3.example/a.jpg", status="success",
        created_at=datetime.now(timezone.utc), **extra,
    )
    db.add(work)
    db.commit()
    return work


def _mock_final(db, student, admin):
    """Финал пробника по рисунку — единственная плашка точки А у ученика."""
    assignment = ExamAssignment(
        title="Пробник", subject="Рисунок", kind="mock", created_by_id=admin.id, status="published",
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=1, title="Билет",
        start_date=date.today() - timedelta(days=1), end_date=date.today() + timedelta(days=1),
    )
    db.add(ticket)
    db.flush()
    cycle = ExamCycle(user_id=student.id, subject="Рисунок", ticket_id=ticket.id,
                      started_at=datetime.now(timezone.utc))
    db.add(cycle)
    db.flush()
    return _work(db, student, WORK_TYPE_MOCK_EXAM, subject="Рисунок", cycle_id=cycle.id, is_final=True)


def _score(client, session_factory, admin, route, student, db):
    client.cookies.set("session_id", session_factory(admin).id)
    if route == "admin_work":
        work = _mock_final(db, student, admin)
        return client.post(f"/cabinet/admin/works/{work.id}/score", data={"score": "80"})
    if route == "card_work":
        work = _mock_final(db, student, admin)
        return client.post(f"/cabinet/students/{student.id}/works/{work.id}/score", data={"score": "80"})
    if route == "portfolio_before":
        _work(db, student, WORK_TYPE_BEFORE)
        return client.post(
            f"/cabinet/staff/students-review/portfolio-before/{student.id}/score", json={"score": 80},
        )
    _work(db, student, WORK_TYPE_AFTER)
    return client.post(f"/cabinet/staff/point-a/{student.id}/portfolio-after/score", json={"score": 80})


@pytest.mark.parametrize("route", ["admin_work", "card_work", "portfolio_before", "portfolio_after"])
def test_last_point_a_score_notifies_student_level(
    client, db, admin, student, session_factory, telegram, route
):
    resp = _score(client, session_factory, admin, route, student, db)

    assert resp.status_code in (200, 302, 303), resp.text
    db.refresh(student)
    assert student.point_a_notified_at is not None  # уровень сообщён ровно этим баллом
    # Балл за пробник шлёт и своё уведомление, поэтому их может быть два.
    assert db.query(Notification).filter(Notification.user_id == student.id).count() >= 1
    assert {call.args[0] for call in telegram.await_args_list} == {CHAT_ID}


def test_unfinished_point_a_sends_nothing(client, db, admin, student, session_factory, telegram):
    """Две плашки, оценена одна — точка А не разобрана, уведомления нет."""
    _work(db, student, WORK_TYPE_AFTER)
    resp = _score(client, session_factory, admin, "portfolio_before", student, db)

    assert resp.status_code == 200
    db.refresh(student)
    assert student.point_a_notified_at is None
    assert db.query(Notification).filter(Notification.user_id == student.id).count() == 0
