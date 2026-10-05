"""Правка отправленной ОС сотрудником (владелец 05.10.2026).

Правила живут в `services/feedback_edit.py`; сторожа держат их через
настоящие роуты обоих диалогов — сдачи в задании и пробника:

- в задании автор правит своё, пока ОС открыта; после «Завершить ОС» — только
  если ГП или суперадмин вернул её на правку;
- правится текст, вложение можно только убрать;
- после оценки ученика правка закрыта везде;
- в пробнике правит только автор этой ОС;
- правка оставляет пометку «изменено».
"""

from unittest.mock import patch

import pytest

from app.models.feedback import FeedbackMessage
from app.models.notification import Notification
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from tests.test_feedback_rating import (
    _close,
    _login,
    _mock_dialog,
    _rate,
    _setup_block,
    _staff_says,
)


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    with patch("app.api.task_block_feedback.notify"), \
         patch("app.api.feedback.notify"), \
         patch("app.services.feedback_rating.s3_service.upload_to_s3",
               side_effect=lambda path, data, ct: f"https://s3.test/{path}"), \
         patch("app.services.feedback_rating.telegram_service.send_message"):
        yield


def _edit_url(submission, message):
    return f"/cabinet/staff/task-block-submissions/{submission.id}/messages/{message.id}/edit"


def _edit(client, session_factory, staff, submission, message, text, remove=()):
    _login(client, session_factory, staff)
    return client.post(
        _edit_url(submission, message), data={"text": text, "remove": list(remove)},
    )


def _staff_message(db, submission):
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    db.refresh(feedback)
    return feedback, feedback.messages[-1]


def _titles(db, user):
    return [n.title for n in db.query(Notification).filter(Notification.user_id == user.id).all()]


# ── Сдача в задании: открытая ОС ─────────────────────────────────────────────

def test_author_edits_own_message_while_open(db, user_factory, session_factory, client):
    """Пока ОС открыта, автор правит своё сообщение сам, как в Telegram.
    Ученику — пометка «изменено», без уведомления на каждую опечатку."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=993_001)
    _staff_says(client, session_factory, curator, submission, "Тень слабая")
    _feedback, message = _staff_message(db, submission)

    response = _edit(client, session_factory, curator, submission, message, "Тень плотнее")

    assert response.json() == {"ok": True}
    db.refresh(message)
    assert message.text == "Тень плотнее"
    assert message.edited_at is not None
    assert "Обратная связь обновлена" not in _titles(db, student)
    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback").text
    assert "изменено" in page
    assert "data-edit-form" not in page


def test_only_own_staff_message_is_editable(db, user_factory, session_factory, client):
    curator, student, _task, submission = _setup_block(db, user_factory, base=993_011)
    head = user_factory(vk_id=993_013, name="ГП Правка", role_name="админ")
    _staff_says(client, session_factory, curator, submission, "Разбор")
    _feedback, message = _staff_message(db, submission)

    assert _edit(client, session_factory, head, submission, message, "Чужая правка").status_code == 403
    _login(client, session_factory, student)
    assert client.post(_edit_url(submission, message), data={"text": "x"}).status_code == 403
    db.refresh(message)
    assert message.text == "Разбор"
    _login(client, session_factory, head)
    assert "data-edit-form" not in client.get(
        f"/cabinet/staff/task-block-submissions/{submission.id}/feedback"
    ).text


def test_attachment_can_be_removed_but_message_not_emptied(db, user_factory, session_factory, client):
    """Вложение можно убрать; пустым сообщение не остаётся."""
    curator, _student, _task, submission = _setup_block(db, user_factory, base=993_021)
    _staff_says(client, session_factory, curator, submission, "Смотри фото")
    feedback, _ = _staff_message(db, submission)
    photo = TaskBlockFeedbackMessage(
        feedback_id=feedback.id, sender_id=curator.id, sender_role="curator",
        photo_s3_path="fb/1.jpg", photo_s3_url="https://s3.test/fb/1.jpg",
    )
    db.add(photo)
    db.commit()

    emptied = _edit(client, session_factory, curator, submission, photo, "", remove=["photo"])
    assert emptied.status_code == 422
    assert "пустым" in emptied.json()["error"]

    response = _edit(client, session_factory, curator, submission, photo, "Вместо фото – текст", remove=["photo"])
    assert response.status_code == 200
    db.refresh(photo)
    assert (photo.photo_s3_url, photo.photo_s3_path) == (None, None)
    assert photo.text == "Вместо фото – текст"


# ── Сдача в задании: завершённая ОС и возврат на правку ──────────────────────

def test_closed_feedback_is_edited_only_after_return(db, user_factory, session_factory, client):
    """После «Завершить ОС» автор не правит сам. ГП возвращает — автор правит,
    ученику уведомление; «Завершить правку» закрывает правку снова."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=993_031)
    head = user_factory(vk_id=993_033, name="ГП Возврат", role_name="админ")
    _staff_says(client, session_factory, curator, submission, "Разбор")
    _close(client, session_factory, curator, submission)
    _feedback, message = _staff_message(db, submission)
    return_url = f"/cabinet/staff/task-block-submissions/{submission.id}/return-to-author"
    done_url = f"/cabinet/staff/task-block-submissions/{submission.id}/revision-done"

    assert _edit(client, session_factory, curator, submission, message, "Правка").status_code == 403
    _login(client, session_factory, curator)
    assert client.post(return_url).status_code == 403  # куратор сам себе не возвращает

    _login(client, session_factory, head)
    assert client.post(return_url).json() == {"ok": True}
    assert "Обратную связь вернули на правку" in _titles(db, curator)
    assert client.post(return_url).status_code == 409

    assert _edit(client, session_factory, curator, submission, message, "Правка").status_code == 200
    assert "Обратная связь обновлена" in _titles(db, student)
    # Диалог по-прежнему закрыт для новых сообщений.
    assert _staff_says(client, session_factory, curator, submission, "Новое").status_code == 403

    assert client.post(done_url).json() == {"ok": True}
    assert _edit(client, session_factory, curator, submission, message, "Ещё").status_code == 403


def test_open_feedback_is_not_returned(db, user_factory, session_factory, client):
    curator, _student, _task, submission = _setup_block(db, user_factory, base=993_041)
    head = user_factory(vk_id=993_043, name="ГП Открытая", role_name="админ")
    _staff_says(client, session_factory, curator, submission)

    _login(client, session_factory, head)
    response = client.post(f"/cabinet/staff/task-block-submissions/{submission.id}/return-to-author")

    assert response.status_code == 409
    assert "сам" in response.json()["error"]


def test_rated_feedback_is_never_edited(db, user_factory, session_factory, client):
    """Оценка поставлена за конкретный текст: после неё правка закрыта даже
    на возврате, а новый возврат не принимается."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=993_051)
    head = user_factory(vk_id=993_053, name="ГП Оценка", role_name="админ")
    _staff_says(client, session_factory, curator, submission, "Разбор")
    _close(client, session_factory, curator, submission)
    _login(client, session_factory, head)
    client.post(f"/cabinet/staff/task-block-submissions/{submission.id}/return-to-author")
    assert _rate(client, session_factory, student, submission).status_code == 200
    _feedback, message = _staff_message(db, submission)

    response = _edit(client, session_factory, curator, submission, message, "Подмена")

    assert response.status_code == 403
    assert "оценил" in response.json()["error"]
    db.refresh(message)
    assert message.text == "Разбор"


# ── Пробник ──────────────────────────────────────────────────────────────────

def _mock_with_feedback(db, user_factory, session_factory, client, *, base):
    curator, student, cycle, work = _mock_dialog(db, user_factory, base=base)
    _login(client, session_factory, curator)
    client.post(f"/cabinet/feedback/{work.id}/message", data={"text": "Разбор пробника"},
                headers={"Accept": "application/json"})
    message = db.query(FeedbackMessage).order_by(FeedbackMessage.id.desc()).first()
    return curator, student, cycle, work, message


def test_mock_exam_rated_feedback_is_not_edited_or_returned(db, user_factory, session_factory, client, admin_user):
    curator, student, cycle, work, message = _mock_with_feedback(
        db, user_factory, session_factory, client, base=993_101,
    )
    _login(client, session_factory, curator)
    client.post(f"/cabinet/feedback/{work.id}/close-feedback")
    _login(client, session_factory, admin_user)
    assert client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator").status_code == 200
    _login(client, session_factory, student)
    assert client.post(f"/cabinet/feedback/{work.id}/rating",
                       data={"score": "3", "comment": "Мало"}).status_code == 200

    _login(client, session_factory, curator)
    edit = client.post(f"/cabinet/feedback/message/{message.id}/edit", data={"text": "Подмена"})
    assert edit.status_code == 403
    db.refresh(message)
    assert message.text == "Разбор пробника"

    _login(client, session_factory, curator)
    client.post(f"/cabinet/feedback/{cycle.id}/revision-done")
    _login(client, session_factory, admin_user)
    again = client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator",
                        headers={"Accept": "application/json"})
    assert again.status_code == 400
    assert "оценил" in again.json()["detail"]


def test_mock_exam_only_feedback_author_edits(db, user_factory, session_factory, client, admin_user):
    """Возврат цикла открывает правку автору ОС, а не любому сотруднику со
    своим сообщением в цикле."""
    curator, _student, cycle, work, _message = _mock_with_feedback(
        db, user_factory, session_factory, client, base=993_111,
    )
    head = user_factory(vk_id=993_113, name="ГП Пробник", role_name="админ")
    _login(client, session_factory, head)
    client.post(f"/cabinet/feedback/{work.id}/message", data={"text": "Дополню"},
                headers={"Accept": "application/json"})
    head_message = db.query(FeedbackMessage).filter_by(sender_id=head.id).one()
    _login(client, session_factory, admin_user)
    client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator")

    _login(client, session_factory, head)
    response = client.post(f"/cabinet/feedback/message/{head_message.id}/edit", data={"text": "Правка"},
                           headers={"Accept": "application/json"})

    assert response.status_code == 403
    assert "автор" in response.json()["detail"]


def test_mock_exam_edit_marks_message_edited(db, user_factory, session_factory, client, admin_user):
    curator, student, cycle, _work, message = _mock_with_feedback(
        db, user_factory, session_factory, client, base=993_121,
    )
    _login(client, session_factory, admin_user)
    client.post(f"/cabinet/superadmin/feedback/{cycle.id}/return-to-curator")
    _login(client, session_factory, curator)
    page = client.get(f"/cabinet/curator/feedback/{cycle.id}").text
    assert f'startEditMsg({message.id})' in page

    assert client.post(f"/cabinet/feedback/message/{message.id}/edit",
                       data={"text": "Исправлено"}).status_code == 200

    db.refresh(message)
    assert message.edited_at is not None
    _login(client, session_factory, student)
    assert "изменено" in client.get(f"/cabinet/feedback/{cycle.id}").text
