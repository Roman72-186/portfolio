"""Точка А (Фаза 1): пять мест простановки балла обязаны звать
`maybe_notify_point_a_level` в той же транзакции, что и сам балл (см.
`AGENTS.md` и докстринг функции). Здесь — не пересчёт уровня (это
`test_point_a_notify.py`), а сама проводка: что каждый роут действительно
вызывает функцию с правильным учеником и рассылает результат, если он не
None.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.models.notification import Notification
from app.models.work import Work, WORK_TYPE_MOCK_EXAM


@pytest.fixture()
def admin(user_factory):
    return user_factory(vk_id=863_001, name="ГП", role_name="админ")


@pytest.fixture()
def student(user_factory):
    return user_factory(vk_id=863_002, name="Ученик")


def _as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def _mk_work(db, student_id, **overrides):
    work = Work(
        user_id=student_id, work_type=WORK_TYPE_MOCK_EXAM, month="сентябрь", year=2026,
        filename="a.jpg", status="success",
    )
    for k, v in overrides.items():
        setattr(work, k, v)
    db.add(work)
    db.commit()
    return work


def _spy():
    """Возвращает None (как обычно, пока точка А не разобрана) и фиксирует вызовы."""
    return MagicMock(return_value=None)


def test_admin_score_work_calls_point_a_hook(client, db, admin, student, session_factory):
    work = _mk_work(db, student.id)
    _as(client, session_factory, admin)
    spy = _spy()

    with patch("app.api.cabinet_admin.maybe_notify_point_a_level", spy):
        resp = client.post(
            f"/cabinet/admin/works/{work.id}/score",
            data={"score": "80"},
        )

    assert resp.status_code in (200, 302)
    assert spy.call_count == 1
    assert spy.call_args.args[1].id == student.id


def test_curator_score_work_calls_point_a_hook(client, db, admin, student, session_factory):
    work = _mk_work(db, student.id)
    _as(client, session_factory, admin)
    spy = _spy()

    with patch("app.api.cabinet_students_shared.maybe_notify_point_a_level", spy):
        resp = client.post(
            f"/cabinet/students/{student.id}/works/{work.id}/score",
            data={"score": "80"},
        )

    assert resp.status_code in (200, 302)
    assert spy.call_count == 1
    assert spy.call_args.args[1].id == student.id


def test_send_to_retake_calls_point_a_hook(client, db, admin, student, session_factory):
    work = _mk_work(db, student.id)
    _as(client, session_factory, admin)
    spy = _spy()

    with patch("app.api.cabinet_students_shared.maybe_notify_point_a_level", spy):
        resp = client.post(
            f"/cabinet/students/{student.id}/mock-exams/{work.id}/retake",
            data={"score": "50", "comment": "переделать"},
        )

    assert resp.status_code == 200
    assert spy.call_count == 1
    assert spy.call_args.args[1].id == student.id


def test_portfolio_before_calls_point_a_hook(client, db, admin, student, session_factory):
    _as(client, session_factory, admin)
    spy = _spy()

    with patch("app.api.student_review.maybe_notify_point_a_level", spy):
        resp = client.post(
            f"/cabinet/staff/students-review/portfolio-before/{student.id}/score",
            json={"score": 80},
        )

    assert resp.status_code == 200
    assert spy.call_count == 1
    assert spy.call_args.args[1].id == student.id


def test_portfolio_after_calls_point_a_hook(client, db, admin, student, session_factory):
    _as(client, session_factory, admin)
    spy = _spy()

    with patch("app.api.cabinet_point_a.maybe_notify_point_a_level", spy):
        resp = client.post(
            f"/cabinet/staff/point-a/{student.id}/portfolio-after/score",
            json={"score": 80},
        )

    assert resp.status_code == 200
    assert spy.call_count == 1
    assert spy.call_args.args[1].id == student.id


def test_notification_from_hook_is_dispatched(client, db, admin, student, session_factory):
    """Когда хук вернул уведомление — оно реально уходит фоновой задачей
    в `notify()`, а не просто создаётся в базе."""
    _as(client, session_factory, admin)
    fake_notification = Notification(user_id=student.id, title="Точка А разобрана — уровень 1")
    db.add(fake_notification)
    db.commit()
    spy = MagicMock(return_value=fake_notification)

    with (
        patch("app.api.cabinet_point_a.maybe_notify_point_a_level", spy),
        patch("app.api.cabinet_point_a.notify") as notify_spy,
    ):
        resp = client.post(
            f"/cabinet/staff/point-a/{student.id}/portfolio-after/score",
            json={"score": 80},
        )

    assert resp.status_code == 200
    called_ids = [call.args[0] for call in notify_spy.call_args_list]
    assert fake_notification.id in called_ids
