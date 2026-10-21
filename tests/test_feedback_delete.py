"""Удаление ошибочной ОС по сдаче в задании (владелец 21.10.2026).

Правило — `services/feedback_edit.py::task_block_delete_refusal`, удаление —
`delete_task_block_feedback`. Сторожа держат их через настоящий роут:

- удаляют только ГП и суперадмин, куратор — нет, даже своё;
- только пока ОС не завершена и ученик не ответил;
- уходят диалог, его сообщения и уведомления ученику о нём, работа
  возвращается в очередь непроверенной, балл и уведомление о балле остаются;
- удаление пишется в журнал `audit_logs`.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.task_block import TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from tests.test_feedback_rating import _close, _login, _setup_block, _staff_says


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    with patch("app.api.task_block_feedback.notify"), \
         patch("app.services.feedback_rating.telegram_service.send_message"):
        yield


# Атрибут самой кнопки: текст «Удалить обратную связь» есть и в скрипте страницы.
DELETE_BUTTON = 'data-delete-feedback="'


def _delete(client, session_factory, staff, submission):
    _login(client, session_factory, staff)
    return client.post(f"/cabinet/staff/task-block-submissions/{submission.id}/delete-feedback")


def _staff_page(client, session_factory, staff, submission):
    _login(client, session_factory, staff)
    return client.get(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback").text


def _feedback(db, submission):
    return db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one_or_none()


def test_head_deletes_wrong_feedback_and_work_returns_to_queue(
    db, user_factory, session_factory, client,
):
    curator, student, _task, submission = _setup_block(db, user_factory, base=994_001)
    head = user_factory(vk_id=994_003, name="ГП Удаление", role_name="админ")
    _staff_says(client, session_factory, curator, submission, "ОС не тому ученику")
    _staff_says(client, session_factory, curator, submission, "И ещё одно")
    submission.reviewed_at = datetime.now(timezone.utc)
    submission.reviewed_by_id = curator.id
    submission.score = 80
    db.add(Notification(
        user_id=student.id, title="Преподаватель оценил работу", text="80 / 100.",
        task_block_submission_id=submission.id,
    ))
    db.commit()
    feedback_id = _feedback(db, submission).id

    assert DELETE_BUTTON in _staff_page(client, session_factory, head, submission)
    assert _delete(client, session_factory, head, submission).json() == {"ok": True}

    db.expire_all()
    assert _feedback(db, submission) is None
    assert not db.query(TaskBlockFeedbackMessage).filter_by(feedback_id=feedback_id).all()
    titles = [n.title for n in db.query(Notification).filter_by(user_id=student.id).all()]
    assert titles == ["Преподаватель оценил работу"]
    submission = db.get(TaskBlockSubmission, submission.id)
    assert (submission.reviewed_at, submission.reviewed_by_id) == (None, None)
    assert int(submission.score) == 80
    log = db.query(AuditLog).filter_by(action="task_block_feedback_delete").one()
    assert (log.performed_by_id, log.target_user_id) == (head.id, student.id)
    details = json.loads(log.details)
    assert (details["feedback_id"], details["author_id"], details["messages"]) == (feedback_id, curator.id, 2)
    assert details["text_start"] == "ОС не тому ученику"

    # Ученик открывает старую ссылку из Telegram — диалог пуст.
    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert page.status_code == 200
    assert "ОС не тому ученику" not in page.text


def test_curator_cannot_delete_even_own_feedback(db, user_factory, session_factory, client):
    curator, _student, _task, submission = _setup_block(db, user_factory, base=994_011)
    _staff_says(client, session_factory, curator, submission, "Разбор")

    assert DELETE_BUTTON not in _staff_page(client, session_factory, curator, submission)
    assert _delete(client, session_factory, curator, submission).status_code == 403
    assert _feedback(db, submission) is not None


def test_closed_feedback_is_not_deleted(db, user_factory, session_factory, client):
    """Завершённую ОС не удаляют — её возвращают автору на правку."""
    curator, _student, _task, submission = _setup_block(db, user_factory, base=994_021)
    head = user_factory(vk_id=994_023, name="ГП Закрыта", role_name="админ")
    _staff_says(client, session_factory, curator, submission, "Разбор")
    _close(client, session_factory, curator, submission)

    assert DELETE_BUTTON not in _staff_page(client, session_factory, head, submission)
    response = _delete(client, session_factory, head, submission)
    assert response.status_code == 409
    assert "завершена" in response.json()["error"]
    assert _feedback(db, submission) is not None


def test_feedback_with_student_reply_is_not_deleted(db, user_factory, session_factory, client):
    curator, student, _task, submission = _setup_block(db, user_factory, base=994_031)
    head = user_factory(vk_id=994_033, name="ГП Ответ", role_name="админ")
    _staff_says(client, session_factory, curator, submission, "Разбор")
    db.add(TaskBlockFeedbackMessage(
        feedback_id=_feedback(db, submission).id, sender_id=student.id,
        sender_role="student", text="Спасибо!",
    ))
    db.commit()

    response = _delete(client, session_factory, head, submission)
    assert response.status_code == 409
    assert "ответил" in response.json()["error"]
    assert _feedback(db, submission) is not None


def test_nothing_to_delete_without_feedback(db, user_factory, session_factory, client):
    _curator, _student, _task, submission = _setup_block(db, user_factory, base=994_041)
    head = user_factory(vk_id=994_043, name="ГП Пусто", role_name="админ")

    assert _delete(client, session_factory, head, submission).status_code == 404
