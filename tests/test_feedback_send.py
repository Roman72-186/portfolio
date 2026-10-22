"""ОС уходит ученику по кнопке «Отправить ОС» (владелец 22.10.2026).

Служба заботы 21.10.2026: на одну ОС ученику приходило 5–15 одинаковых
уведомлений — по одному на каждое сообщение сотрудника. Теперь сообщения
сотрудника копятся черновиком, «Отправить ОС» открывает их ученику одним
уведомлением, «Завершить ОС» появляется, когда ученику больше нечего
спросить, а о забытом черновике напоминает планировщик. Правила —
`services/task_block_feedback.py` (`close_refusal`, `send_drafts`,
`run_unsent_feedback_reminders`), инвариант — `docs/invariants/feedback.md`."""

from datetime import datetime, timedelta, timezone

from app.constants import TARIFF_WITH_YOU
from app.models.notification import Notification
from app.models.student_reminder import KIND_FEEDBACK_UNSENT, StudentReminder
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.services.task_block_feedback import run_unsent_feedback_reminders
from tests.test_feedback_rating import (
    _close,
    _login,
    _quiet_side_effects,  # noqa: F401 — autouse-заглушки уведомлений и S3
    _send,
    _setup_block,
    _staff_says,
)

FEEDBACK_TITLES = (
    "Преподаватель оставил обратную связь по работе",
    "Преподаватель оставил обратную связь по работам",
)


def _student_feedback_notes(db, student):
    return db.query(Notification).filter(
        Notification.user_id == student.id, Notification.title.in_(FEEDBACK_TITLES),
    ).all()


def _student_asks(client, session_factory, student, submission, text="Вопрос"):
    _login(client, session_factory, student)
    return client.post(
        f"/cabinet/task-block-submissions/{submission.id}/messages", data={"text": text},
    )


def _messages(db, submission):
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    db.refresh(feedback)
    return feedback.messages


def test_drafts_send_as_one_notification(db, user_factory, session_factory, client):
    """Текст, фото, ещё текст — ни одного уведомления; «Отправить ОС» — одно."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=994_001)
    for text in ("Общий разбор", "Правка по свету", "Правка по композиции"):
        response = _staff_says(client, session_factory, curator, submission, text, send=False)
        assert response.status_code == 200
    assert _student_feedback_notes(db, student) == []
    assert all(message.is_draft for message in _messages(db, submission))

    _login(client, session_factory, curator)
    assert _send(client, submission).status_code == 200
    assert len(_student_feedback_notes(db, student)) == 1
    assert not any(message.is_draft for message in _messages(db, submission))

    again = _send(client, submission)
    assert again.status_code == 409
    assert len(_student_feedback_notes(db, student)) == 1


def test_student_does_not_see_drafts(db, user_factory, session_factory, client):
    """Черновик не виден ученику: ни в диалоге, ни во вкладке «Обратная связь»."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=994_011)
    _staff_says(client, session_factory, curator, submission, "Секретный черновик", send=False)

    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert page.status_code == 200
    assert "Секретный черновик" not in page.text
    assert "Преподаватель ещё не оставил обратную связь" in page.text
    tab = client.get("/cabinet/cycle")
    assert "Домашка недели" not in tab.text

    _login(client, session_factory, curator)
    staff_page = client.get(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback")
    assert "Секретный черновик" in staff_page.text
    assert "не отправлено ученику" in staff_page.text
    assert "Отправить ОС" in staff_page.text

    _send(client, submission)
    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert "Секретный черновик" in page.text


def test_student_cannot_reply_to_draft(db, user_factory, session_factory, client):
    """Вопрос можно задать только по отправленной ОС."""
    curator, student, _task, submission = _setup_block(
        db, user_factory, base=994_021, dialog_tariffs=[TARIFF_WITH_YOU],
    )
    _staff_says(client, session_factory, curator, submission, send=False)
    assert _student_asks(client, session_factory, student, submission).status_code == 403

    _login(client, session_factory, curator)
    _send(client, submission)
    assert _student_asks(client, session_factory, student, submission).status_code == 200


def test_student_reply_still_notifies_teacher_at_once(db, user_factory, session_factory, client):
    """Ответ ученика черновиком не становится — преподаватель узнаёт сразу."""
    curator, student, _task, submission = _setup_block(
        db, user_factory, base=994_031, dialog_tariffs=[TARIFF_WITH_YOU],
    )
    _staff_says(client, session_factory, curator, submission)
    _student_asks(client, session_factory, student, submission)
    notes = db.query(Notification).filter(
        Notification.user_id == curator.id,
        Notification.title == "Ученик ответил по сданной работе",
    ).all()
    assert len(notes) == 1


def test_close_waits_until_student_has_nothing_to_ask(db, user_factory, session_factory, client):
    """«Завершить ОС» — когда лимит потрачен, на последний вопрос ответили и
    ответ отправлен."""
    curator, student, _task, submission = _setup_block(
        db, user_factory, base=994_041, dialog_tariffs=[TARIFF_WITH_YOU],
    )
    _staff_says(client, session_factory, curator, submission)
    refused = _close(client, session_factory, curator, submission)
    assert refused.status_code == 409
    assert "осталось 2 сообщения" in refused.json()["error"]

    _student_asks(client, session_factory, student, submission, "Вопрос 1")
    _student_asks(client, session_factory, student, submission, "Вопрос 2")
    refused = _close(client, session_factory, curator, submission)
    assert refused.status_code == 409
    assert "Ответь на последний вопрос" in refused.json()["error"]

    _staff_says(client, session_factory, curator, submission, "Ответ", send=False)
    refused = _close(client, session_factory, curator, submission)
    assert refused.status_code == 409
    assert "Отправить ОС" in refused.json()["error"]

    _send(client, submission)
    assert _close(client, session_factory, curator, submission).json() == {
        "ok": True, "closed_now": True,
    }


def test_close_right_after_send_when_reply_is_closed(db, user_factory, session_factory, client):
    """Тариф без ответа: спросить ученик ничего не может — завершить можно
    сразу после отправки, но не по черновику."""
    curator, _student, _task, submission = _setup_block(db, user_factory, base=994_051)
    _staff_says(client, session_factory, curator, submission, send=False)
    assert _close(client, session_factory, curator, submission).status_code == 409

    _send(client, submission)
    assert _close(client, session_factory, curator, submission).json()["closed_now"] is True


def test_staff_page_explains_why_close_is_hidden(db, user_factory, session_factory, client):
    curator, _student, _task, submission = _setup_block(
        db, user_factory, base=994_061, dialog_tariffs=[TARIFF_WITH_YOU],
    )
    _staff_says(client, session_factory, curator, submission)
    _login(client, session_factory, curator)
    page = client.get(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback")
    assert "Ученик ещё может задать вопрос" in page.text
    assert "data-feedback-close" not in page.text


def _age_drafts(db, submission, hours):
    moment = datetime.now(timezone.utc) - timedelta(hours=hours)
    for message in _messages(db, submission):
        if message.is_draft:
            message.created_at = moment
    db.commit()


def _reminders(db, user):
    return db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.title == "Обратная связь не отправлена",
    ).all()


def test_unsent_draft_reminds_after_one_hour_two_hours_then_daily(
    db, user_factory, session_factory, client,
):
    """Напоминание автору черновика через час, через два, дальше раз в сутки
    (владелец 22.10.2026); отправленная ОС не напоминает."""
    curator, _student, _task, submission = _setup_block(db, user_factory, base=994_071)
    _staff_says(client, session_factory, curator, submission, send=False)
    start = datetime.now(timezone.utc)

    def run(hours):
        return run_unsent_feedback_reminders(db, now=start + timedelta(hours=hours))

    assert run(0.5) == []
    assert len(run(1.1)) == 1
    note = _reminders(db, curator)[0]
    assert note.task_block_submission_id == submission.id
    assert f"/cabinet/staff/task-block-submissions/{submission.id}/feedback" in note.text
    assert run(1.5) == []
    assert len(run(2.1)) == 1
    assert run(12) == []
    assert len(run(24.1)) == 1
    assert run(40) == []
    assert len(run(48.1)) == 1
    assert db.query(StudentReminder).filter_by(kind=KIND_FEEDBACK_UNSENT).count() == 4
    later = start + timedelta(hours=48.1)

    _login(client, session_factory, curator)
    _send(client, submission)
    assert run_unsent_feedback_reminders(db, now=later + timedelta(days=3)) == []


def test_sent_messages_never_remind(db, user_factory, session_factory, client):
    curator, _student, _task, submission = _setup_block(db, user_factory, base=994_081)
    _staff_says(client, session_factory, curator, submission)
    for message in _messages(db, submission):
        message.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    db.commit()
    assert run_unsent_feedback_reminders(db) == []
    assert db.query(TaskBlockFeedbackMessage).filter_by(is_draft=True).count() == 0


def test_close_opens_after_two_days_of_silence(db, user_factory, session_factory, client):
    """Ученик не тратит вопросы — через двое суток после последнего сообщения
    «Завершить ОС» всё равно появляется (владелец 22.10.2026). Двое суток
    считаются от отправки, а не от черновика."""
    from app.services.task_block_feedback import close_refusal

    curator, student, _task, submission = _setup_block(
        db, user_factory, base=994_091, dialog_tariffs=[TARIFF_WITH_YOU],
    )
    _staff_says(client, session_factory, curator, submission, send=False)
    _age_drafts(db, submission, hours=24 * 5)
    _login(client, session_factory, curator)
    _send(client, submission)

    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    db.refresh(feedback)
    now = datetime.now(timezone.utc)
    refusal = close_refusal(db, submission, feedback, now=now + timedelta(hours=47))
    assert "промолчит до" in refusal
    assert close_refusal(db, submission, feedback, now=now + timedelta(hours=49)) is None

    # Вопрос без ответа держит диалог открытым, сколько бы ни прошло.
    _student_asks(client, session_factory, student, submission)
    db.refresh(feedback)
    assert "Ответь на последний вопрос" in close_refusal(
        db, submission, feedback, now=now + timedelta(days=10),
    )
