"""Сдача домашки учеником + диалог обратной связи (вариант Б, TODO §0 Р2).

Контракт загрузки — как у пробника: одно финальное фото, до
MAX_INTERMEDIATE_PER_SUBMISSION промежуточных. Диалог — куратор пишет первым
(как у Feedback/пробника), студент отвечает только после этого.
"""
from unittest.mock import patch

from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import HomeworkSubmission, HomeworkSubmissionImage
from app.models.notification import Notification
from app.models.tracker import ITEM_HOMEWORK, SOURCE_HOMEWORK
from app.services import s3 as s3_service
from app.services.tracker import create_homework, create_task, set_homework_images

FAKE_URL = "https://s3.example.com/domashka/photo.jpg"


def _homework_task(db, user_id: int, *, max_files: int = 1):
    homework = create_homework(
        db, title="Нарисуй куб", user_id=user_id,
        description="Со всех сторон, карандашом.", submission_required=True, max_files=max_files,
    )
    set_homework_images(db, homework, [{"url": "https://s3.example.com/ref.jpg", "path": "ref.jpg"}])
    task = create_task(
        db, title="Нарисуй куб", user_id=user_id, kind=ITEM_HOMEWORK,
        source_kind=SOURCE_HOMEWORK, source_id=homework.id, assign_to_all=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task, homework


def test_page_creates_submission_lazily_and_shows_description(auth_client, db):
    client, user = auth_client
    task, homework = _homework_task(db, user.id)

    resp = client.get(f"/cabinet/homework/{task.id}")

    assert resp.status_code == 200
    assert "Нарисуй куб" in resp.text
    assert "Со всех сторон, карандашом." in resp.text
    submission = (
        db.query(HomeworkSubmission)
        .filter(HomeworkSubmission.tracker_task_id == task.id, HomeworkSubmission.user_id == user.id)
        .one()
    )
    assert submission.homework_id == homework.id


def test_page_is_404_without_addressing(auth_client, db):
    """Задача есть, но не адресована этому ученику (assign_to_all=False, без тегов)."""
    client, user = auth_client
    homework = create_homework(db, title="Чужое задание", user_id=user.id)
    task = create_task(
        db, title="Чужое задание", user_id=user.id, kind=ITEM_HOMEWORK,
        source_kind=SOURCE_HOMEWORK, source_id=homework.id, assign_to_all=False,
    )
    task.is_published = True
    db.commit()

    resp = client.get(f"/cabinet/homework/{task.id}")
    assert resp.status_code == 404


def test_upload_final_photo(auth_client, db):
    client, user = auth_client
    task, _ = _homework_task(db, user.id)

    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        resp = client.post(
            f"/cabinet/homework/{task.id}/final",
            files={"photo": ("work.jpg", b"fake-bytes", "image/jpeg")},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    image = db.query(HomeworkSubmissionImage).filter(HomeworkSubmissionImage.is_final.is_(True)).one()
    assert image.image_s3_url == FAKE_URL
    submission = db.query(HomeworkSubmission).one()
    assert submission.submitted_at is not None


def test_resubmit_final_replaces_previous(auth_client, db):
    client, user = auth_client
    task, _ = _homework_task(db, user.id)

    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        client.post(f"/cabinet/homework/{task.id}/final", files={"photo": ("a.jpg", b"1", "image/jpeg")})
        client.post(f"/cabinet/homework/{task.id}/final", files={"photo": ("b.jpg", b"2", "image/jpeg")})

    finals = db.query(HomeworkSubmissionImage).filter(HomeworkSubmissionImage.is_final.is_(True)).all()
    assert len(finals) == 1


def test_upload_intermediate_respects_limit(auth_client, db):
    client, user = auth_client
    task, _ = _homework_task(db, user.id, max_files=2)

    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        first = client.post(
            f"/cabinet/homework/{task.id}/intermediate",
            files=[("photos", ("a.jpg", b"1", "image/jpeg")), ("photos", ("b.jpg", b"2", "image/jpeg"))],
        )
        second = client.post(
            f"/cabinet/homework/{task.id}/intermediate",
            files=[("photos", ("c.jpg", b"3", "image/jpeg"))],
        )

    assert first.status_code == 200
    assert first.json()["created"] == 2
    assert second.status_code == 422


def test_student_cannot_message_before_curator(auth_client, db):
    client, user = auth_client
    task, _ = _homework_task(db, user.id)
    client.get(f"/cabinet/homework/{task.id}")  # заводит submission лениво

    resp = client.post(f"/cabinet/homework/{task.id}/message", data={"text": "Привет"})
    assert resp.status_code == 403


def test_curator_first_message_then_student_reply(auth_client, db, user_factory, session_factory):
    client, user = auth_client
    task, _ = _homework_task(db, user.id)
    client.get(f"/cabinet/homework/{task.id}")
    submission = db.query(HomeworkSubmission).one()

    curator = user_factory(vk_id=777_001, name="Куратор Аня", role_name="куратор")
    curator_session = session_factory(curator)
    client.cookies.set("session_id", curator_session.id)

    curator_msg = client.post(
        f"/cabinet/staff/homework/submissions/{submission.id}/message",
        data={"text": "Хорошее начало, доработай тени"},
    )
    assert curator_msg.status_code == 200

    fb = db.query(HomeworkFeedback).filter(HomeworkFeedback.submission_id == submission.id).one()
    assert fb.curator_id == curator.id
    notif = db.query(Notification).filter(Notification.user_id == user.id).one()
    assert "куратор" in notif.title.lower() or "обратн" in notif.title.lower()

    # Ученик снова
    student_session = session_factory(user)
    client.cookies.set("session_id", student_session.id)
    reply = client.post(f"/cabinet/homework/{task.id}/message", data={"text": "Спасибо, поправлю"})
    assert reply.status_code == 200

    messages = (
        db.query(HomeworkFeedbackMessage)
        .filter(HomeworkFeedbackMessage.feedback_id == fb.id)
        .order_by(HomeworkFeedbackMessage.id)
        .all()
    )
    assert [m.sender_role for m in messages] == ["curator", "student"]


def test_staff_submissions_list_shows_submitted_student(admin_client, db, user_factory):
    client, admin = admin_client
    student = user_factory(vk_id=555_001, name="Ученик Петя", role_name="ученик")
    task, homework = _homework_task(db, admin.id)

    from app.services.homework_submission import get_or_create_submission
    submission, _ = get_or_create_submission(db, task=task, user_id=student.id)
    db.commit()

    resp = client.get(f"/cabinet/staff/homework/{task.id}/submissions")
    assert resp.status_code == 200
    assert "Ученик Петя" in resp.text
    assert f"/cabinet/staff/homework/submissions/{submission.id}" in resp.text


def test_student_cannot_open_staff_submissions_list(auth_client, db):
    client, user = auth_client
    task, _ = _homework_task(db, user.id)
    resp = client.get(f"/cabinet/staff/homework/{task.id}/submissions")
    assert resp.status_code == 403
