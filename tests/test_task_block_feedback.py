"""Полный цикл обратной связи по работе, сданной в блоке задания."""

from datetime import datetime, timezone
from unittest.mock import patch

from app.models.notification import Notification
from app.models.task_block import BLOCK_UPLOAD, TaskBlock, TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask


def _submission(db, student, *, legacy_comment=None):
    task = TrackerTask(
        title="Домашняя работа", kind="material", is_published=True, assign_to_all=True,
    )
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Сдать листы")
    db.add(block)
    db.flush()
    submission = TaskBlockSubmission(
        block_id=block.id,
        user_id=student.id,
        submitted_at=datetime.now(timezone.utc),
        review_comment=legacy_comment,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def test_staff_can_score_submission_without_marking_it_reviewed(
    db, user_factory, session_factory, client,
):
    curator = user_factory(vk_id=970_001, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=970_002, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    submission = _submission(db, student)
    _login(client, session_factory, curator)

    with patch("app.api.task_block_feedback.notify"):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/score",
            json={"score": 87},
        )

    assert response.status_code == 200
    db.refresh(submission)
    assert float(submission.score) == 87
    assert submission.scored_by_id == curator.id
    assert submission.scored_at is not None
    assert submission.reviewed_at is None
    notification = db.query(Notification).filter_by(
        user_id=student.id, task_block_submission_id=submission.id
    ).one()
    assert "87 / 100" in notification.text


def test_score_rejects_values_outside_zero_to_hundred(
    admin_client, db, regular_user,
):
    submission = _submission(db, regular_user)
    client, _ = admin_client

    assert client.post(
        f"/cabinet/staff/task-block-submissions/{submission.id}/score", json={"score": -1}
    ).status_code == 422
    assert client.post(
        f"/cabinet/staff/task-block-submissions/{submission.id}/score", json={"score": 101}
    ).status_code == 422


def test_rank_three_cannot_open_foreign_submission(
    db, user_factory, session_factory, client,
):
    owner = user_factory(vk_id=970_003, name="Владелец", role_name="куратор")
    teacher = user_factory(vk_id=970_004, name="Преподаватель", role_name="модератор")
    student = user_factory(vk_id=970_005, name="Ученик")
    student.curator_id = owner.id
    db.commit()
    submission = _submission(db, student)
    _login(client, session_factory, teacher)

    response = client.get(
        f"/cabinet/staff/task-block-submissions/{submission.id}/feedback"
    )

    assert response.status_code == 403
    assert client.post(
        f"/cabinet/staff/task-block-submissions/{submission.id}/score",
        json={"score": 70},
    ).status_code == 403
    assert client.post(
        f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
        data={"text": "Чужая работа"},
    ).status_code == 403


def test_rank_four_can_open_any_submission(
    db, user_factory, session_factory, client,
):
    admin = user_factory(vk_id=970_006, name="Главный", role_name="админ")
    student = user_factory(vk_id=970_007, name="Ученик")
    submission = _submission(db, student, legacy_comment="Старый комментарий")
    _login(client, session_factory, admin)

    response = client.get(
        f"/cabinet/staff/task-block-submissions/{submission.id}/feedback"
    )

    assert response.status_code == 200
    assert "Старый комментарий" in response.text
    assert "Оценка от 0 до 100" in response.text


def test_student_cannot_open_another_students_submission(
    db, user_factory, session_factory, client,
):
    owner = user_factory(vk_id=970_008, name="Первый")
    stranger = user_factory(vk_id=970_009, name="Второй")
    submission = _submission(db, owner)
    _login(client, session_factory, stranger)

    response = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")

    assert response.status_code == 404
    assert client.post(
        f"/cabinet/task-block-submissions/{submission.id}/messages",
        data={"text": "Чужой диалог"},
    ).status_code == 404


def test_mutations_require_real_csrf(admin_client, db, regular_user):
    from app.dependencies import require_csrf, require_csrf_header
    from app.main import app

    submission = _submission(db, regular_user)
    client, _ = admin_client
    form_override = app.dependency_overrides.pop(require_csrf)
    header_override = app.dependency_overrides.pop(require_csrf_header)
    try:
        score = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/score",
            json={"score": 80},
        )
        message = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": "Без токена"},
        )
    finally:
        app.dependency_overrides[require_csrf] = form_override
        app.dependency_overrides[require_csrf_header] = header_override

    assert score.status_code == 403
    assert message.status_code == 403


def test_student_cannot_start_dialog_before_teacher(
    auth_client, db,
):
    client, student = auth_client
    submission = _submission(db, student)

    response = client.post(
        f"/cabinet/task-block-submissions/{submission.id}/messages",
        data={"text": "Когда проверите?"},
    )

    assert response.status_code == 403


def test_teacher_photo_message_opens_dialog_and_notifies_student(
    db, user_factory, session_factory, client,
):
    curator = user_factory(vk_id=970_010, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=970_011, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    submission = _submission(db, student)
    _login(client, session_factory, curator)

    with (
        patch("app.services.task_block_feedback.compress_image", return_value=b"jpeg"),
        patch(
            "app.services.task_block_feedback.s3_service.upload_to_s3",
            return_value="https://s3.example.com/feedback.jpg",
        ),
        patch("app.services.task_block_feedback.s3_service.is_configured", return_value=True),
        patch("app.api.task_block_feedback.notify"),
    ):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": "Исправь перспективу"},
            files={"photo": ("note.jpg", b"image", "image/jpeg")},
        )

    assert response.status_code == 200, response.text
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    message = db.query(TaskBlockFeedbackMessage).filter_by(feedback_id=feedback.id).one()
    assert message.text == "Исправь перспективу"
    assert message.photo_s3_url == "https://s3.example.com/feedback.jpg"
    notification = db.query(Notification).filter_by(
        user_id=student.id, task_block_submission_id=submission.id
    ).one()
    assert "обратную связь" in notification.title.lower()
    _login(client, session_factory, student)
    feed = client.get("/cabinet/notifications/feed").json()
    item = next(row for row in feed["notifications"] if "обратную связь" in row["title"])
    assert item["href"] == f"/cabinet/task-block-submissions/{submission.id}/feedback"


def test_teacher_video_and_audio_message(db, user_factory, session_factory, client):
    """Владелец 17.09.2026: у диалога по блокам задания должны быть те же
    вложения, что у эталонного диалога Feedback — видео-файл и голосовое."""
    curator = user_factory(vk_id=970_012, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=970_013, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    submission = _submission(db, student)
    _login(client, session_factory, curator)

    with (
        patch(
            "app.services.task_block_feedback.s3_service.upload_to_s3",
            return_value="https://s3.example.com/feedback.mp4",
        ),
        patch("app.api.task_block_feedback.notify"),
    ):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": "Разбор голосом"},
            files={
                "video": ("review.mp4", b"video-bytes", "video/mp4"),
                "audio": ("review.mp3", b"audio-bytes", "audio/mpeg"),
            },
        )

    assert response.status_code == 200, response.text
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    message = db.query(TaskBlockFeedbackMessage).filter_by(feedback_id=feedback.id).one()
    assert message.video_s3_url == "https://s3.example.com/feedback.mp4"
    assert message.audio_s3_url == "https://s3.example.com/feedback.mp4"

    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert page.status_code == 200
    assert 'class="hw-msg-video"' in page.text
    assert 'class="hw-msg-audio"' in page.text


def test_video_message_rejects_bad_type(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=970_016, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=970_017, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    submission = _submission(db, student)
    _login(client, session_factory, curator)

    response = client.post(
        f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
        data={"text": "текст"},
        files={"video": ("bad.txt", b"not-a-video", "text/plain")},
    )
    assert response.status_code == 422


def test_failed_photo_upload_does_not_save_text_only_message(
    db, user_factory, session_factory, client,
):
    curator = user_factory(vk_id=970_014, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=970_015, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    submission = _submission(db, student)
    _login(client, session_factory, curator)

    with (
        patch("app.services.task_block_feedback.compress_image", return_value=b"jpeg"),
        patch("app.services.task_block_feedback.s3_service.upload_to_s3", return_value=None),
    ):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": "Текст с обязательным фото"},
            files={"photo": ("note.jpg", b"image", "image/jpeg")},
        )

    assert response.status_code == 422
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    assert db.query(TaskBlockFeedbackMessage).filter_by(feedback_id=feedback.id).count() == 0


def test_student_can_reply_after_teacher_started_dialog(
    db, user_factory, session_factory, client,
):
    curator = user_factory(vk_id=970_012, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=970_013, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    submission = _submission(db, student)
    _login(client, session_factory, curator)
    with patch("app.api.task_block_feedback.notify"):
        first = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": "Доработай фон"},
        )
    assert first.status_code == 200

    _login(client, session_factory, student)
    with patch("app.api.task_block_feedback.notify"):
        reply = client.post(
            f"/cabinet/task-block-submissions/{submission.id}/messages",
            data={"text": "Готово, загрузил новую версию"},
        )

    assert reply.status_code == 200
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    messages = db.query(TaskBlockFeedbackMessage).filter_by(
        feedback_id=feedback.id
    ).order_by(TaskBlockFeedbackMessage.id).all()
    assert [message.text for message in messages] == [
        "Доработай фон", "Готово, загрузил новую версию",
    ]
    assert db.query(Notification).filter_by(
        user_id=curator.id, task_block_submission_id=submission.id
    ).count() == 1

    _login(client, session_factory, curator)
    feed = client.get("/cabinet/notifications/feed").json()
    item = next(row for row in feed["notifications"] if row["title"].startswith("Ученик"))
    assert item["href"] == (
        f"/cabinet/staff/task-block-submissions/{submission.id}/feedback"
    )
    page = client.get("/cabinet/staff/notifications")
    assert page.status_code == 200
    assert f'/cabinet/staff/task-block-submissions/{submission.id}/feedback' in page.text
