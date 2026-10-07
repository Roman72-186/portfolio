"""Сданную работу запирает только обратная связь преподавателя (владелец 07.10.2026).

«Закрыть смену фото только после ОС от преподавателя.» Статус задания
«выполнено» — кнопкой «Завершить задание» или автозакрытием по последнему
шагу — правку не запирает. С 02.10.2026 запирал, и в задании, где сдача —
последний шаг, «Заменить фото» пропадало сразу после отправки. Правило живёт
в одном месте — `submission_edit.block_work_reason`.
"""
from datetime import timedelta, timezone
from unittest.mock import patch

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_PHOTO_UPLOAD,
    BLOCK_TEXT,
    TaskBlock,
    TaskBlockSubmissionImage,
)
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTaskState
from app.services import s3 as s3_service
from app.services.program import day_bounds
from app.services.task_blocks import get_submission
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

FAKE_URL = "https://s3.example.com/zadaniya/work.jpg"
NEW_URL = "https://s3.example.com/zadaniya/new.jpg"
TODAY = today_msk()
REVIEWED = "Преподаватель уже проверил работу. Изменить её нельзя."
REPLIED = "Преподаватель уже ответил по работе. Изменить её нельзя."


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _task(db, owner):
    topic = LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(TODAY - timedelta(days=1))),
        ends_at=_utc(msk_midnight(TODAY + timedelta(days=6))),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    task = create_task(
        db, title="Эскиз", user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY + timedelta(days=2))[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, *, order=1, required=True, block_type=BLOCK_PHOTO_UPLOAD, photos=None):
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title=f"Шаг {order}",
        sort_order=order, is_required=required, required_photos=photos,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _upload(client, block_id, n=1, *, replace=False, url=FAKE_URL):
    data = {"comment": ""}
    if replace:
        data["replace"] = "1"
    files = [("photos", (f"w{i}.jpg", b"fake-bytes", "image/jpeg")) for i in range(n)]
    with patch.object(s3_service, "upload_to_s3", return_value=url):
        return client.post(
            f"/cabinet/tracker/blocks/{block_id}/upload", files=files, data=data,
        )


def _urls(db, submission):
    return sorted(
        i.image_s3_url for i in db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id == submission.id)
    )


def _task_status(db, task, user):
    state = db.query(TrackerTaskState).filter_by(task_id=task.id, user_id=user.id).one_or_none()
    return state.status if state else None


def _payload(client, task, block):
    blocks = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"]
    return next(b for b in blocks if b["id"] == block.id)


def _autoclosed(client, db, user):
    """Задание из одного блока сдачи: отправка фото сама закрывает задание —
    как «Рисунок» в цикле «Октябрь», где жалоба и возникла."""
    task = _task(db, user)
    block = _block(db, task, photos=1)
    assert _upload(client, block.id).status_code == 200
    assert _task_status(db, task, user) == "done"
    return task, block


def test_autoclosed_task_allows_replace(auth_client, db):
    """Сдача — последний шаг: задание закрылось само, а «Заменить фото» работает."""
    client, user = auth_client
    task, block = _autoclosed(client, db, user)

    assert _payload(client, task, block)["edit_reason"] is None
    resp = _upload(client, block.id, replace=True, url=NEW_URL)

    assert resp.status_code == 200, resp.text
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert _urls(db, submission) == [NEW_URL]


def test_button_completion_keeps_work_editable(auth_client, db):
    """Ученик нажал «Завершить задание» — догрузка, удаление и описание открыты."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task)
    # Необязательный шаг: задание само не закроется, у ученика есть кнопка.
    _block(db, task, order=2, required=False)
    assert _upload(client, block.id).status_code == 200
    assert _upload(client, block.id).status_code == 200
    done = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert done.status_code == 200, done.text
    assert _task_status(db, task, user) == "done"

    submission = get_submission(db, block_id=block.id, user_id=user.id)
    image = db.query(TaskBlockSubmissionImage).filter_by(submission_id=submission.id).first()
    more = _upload(client, block.id, url=NEW_URL)
    deleted = client.post(f"/cabinet/tracker/blocks/{block.id}/images/{image.id}/delete")
    comment = client.post(
        f"/cabinet/tracker/blocks/{block.id}/comment", json={"comment": "Переделал"}
    )

    for resp in (more, deleted, comment):
        assert resp.status_code == 200, resp.text
    db.refresh(submission)
    assert NEW_URL in _urls(db, submission)
    assert submission.comment == "Переделал"


def test_review_locks_replace_in_done_task(auth_client, db):
    """Проверка преподавателя запирает замену — и экран прячет кнопку."""
    client, user = auth_client
    task, block = _autoclosed(client, db, user)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    submission.score = 80
    db.commit()

    assert _payload(client, task, block)["edit_reason"] == REVIEWED
    resp = _upload(client, block.id, replace=True, url=NEW_URL)

    assert resp.status_code == 409
    assert resp.json()["error"] == REVIEWED
    assert _urls(db, submission) == [FAKE_URL]


def test_teacher_reply_locks_replace_in_done_task(auth_client, db, user_factory):
    """Ответ преподавателя в диалоге по работе — тоже ОС, замена закрыта."""
    client, user = auth_client
    task, block = _autoclosed(client, db, user)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    curator = user_factory(vk_id=777_031, name="Куратор", role_name="куратор")
    feedback = TaskBlockFeedback(submission_id=submission.id, curator_id=curator.id)
    db.add(feedback)
    db.flush()
    # Явно после сдачи: SQLite теряет зону, и без сдвига ответ оказывался бы
    # раньше сдачи (подробно — `test_block_upload.py`).
    db.add(TaskBlockFeedbackMessage(
        feedback_id=feedback.id, sender_id=curator.id, sender_role="curator", text="Исправь",
        created_at=submission.submitted_at + timedelta(minutes=1),
    ))
    db.commit()

    resp = _upload(client, block.id, replace=True, url=NEW_URL)

    assert resp.status_code == 409
    assert resp.json()["error"] == REPLIED
    assert _urls(db, submission) == [FAKE_URL]


def test_returned_work_reopens_after_review(auth_client, db):
    """Возврат на доработку главнее проверки: ученик снова меняет работу."""
    client, user = auth_client
    task, block = _autoclosed(client, db, user)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    submission.score = 40
    submission.needs_revision = True
    db.commit()

    assert _payload(client, task, block)["edit_reason"] is None
    resp = _upload(client, block.id, replace=True, url=NEW_URL)

    assert resp.status_code == 200
    assert _urls(db, submission) == [NEW_URL]


def test_empty_block_of_a_done_task_takes_a_first_upload(auth_client, db):
    """Пустой необязательный блок после «Завершить задание» первую сдачу принимает."""
    client, user = auth_client
    task = _task(db, user)
    _block(db, task, block_type=BLOCK_TEXT)
    spare = _block(db, task, order=2, required=False)
    done = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert done.status_code == 200, done.text

    assert _upload(client, spare.id).status_code == 200
