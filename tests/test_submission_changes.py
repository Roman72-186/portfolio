"""История правок сданной работы (владелец 07.10.2026).

«Нужно фиксировать сколько и когда были замены фото в заданиях.» Каждая
правка уже сданной работы — замена всех фото, удаление одного, догрузка —
пишется с временем, числом фото до и после и ссылками на убранные фото.
Видна проверяющему в карточке проверки, ученику — нет.
"""
from app.models.task_block import (
    CHANGE_ADD,
    CHANGE_DELETE,
    CHANGE_REPLACE,
    TaskBlockSubmissionChange,
    TaskBlockSubmissionChangeImage,
    TaskBlockSubmissionImage,
)
from app.services.task_blocks import get_submission
from tests.test_work_locked_by_feedback import FAKE_URL, NEW_URL, _block, _task, _upload


def _changes(db, submission):
    return (
        db.query(TaskBlockSubmissionChange)
        .filter_by(submission_id=submission.id)
        .order_by(TaskBlockSubmissionChange.id)
        .all()
    )


def _removed_urls(db, change):
    return [
        image.image_s3_url
        for image in db.query(TaskBlockSubmissionChangeImage).filter_by(change_id=change.id)
    ]


def test_replace_records_time_counts_and_old_photos(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, photos=1)
    assert _upload(client, block.id).status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert _changes(db, submission) == []

    assert _upload(client, block.id, replace=True, url=NEW_URL).status_code == 200
    assert _upload(client, block.id, replace=True, url=NEW_URL).status_code == 200

    changes = _changes(db, submission)
    assert [c.kind for c in changes] == [CHANGE_REPLACE, CHANGE_REPLACE]
    assert (changes[0].photos_before, changes[0].photos_after) == (1, 1)
    assert changes[0].created_at is not None
    assert _removed_urls(db, changes[0]) == [FAKE_URL]
    assert _removed_urls(db, changes[1]) == [NEW_URL]


def test_partial_submission_is_not_a_change(auth_client, db):
    """«2 из 3» ещё не сдано: догрузка до полной сдачи — сдача, не правка."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, photos=3)
    assert _upload(client, block.id, n=2).status_code == 200
    assert _upload(client, block.id, n=1).status_code == 200

    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert submission.submitted_at is not None
    assert _changes(db, submission) == []


def test_delete_and_add_after_submission_are_recorded(auth_client, db):
    """Блок без заданного числа фото: удаление и догрузка — тоже правки."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task)
    _block(db, task, order=2)  # задание открыто, правка не заперта
    assert _upload(client, block.id, n=2).status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    image = db.query(TaskBlockSubmissionImage).filter_by(submission_id=submission.id).first()

    deleted = client.post(f"/cabinet/tracker/blocks/{block.id}/images/{image.id}/delete")
    assert deleted.status_code == 200, deleted.text
    assert _upload(client, block.id, url=NEW_URL).status_code == 200

    delete, add = _changes(db, submission)
    assert (delete.kind, delete.photos_before, delete.photos_after) == (CHANGE_DELETE, 2, 1)
    assert _removed_urls(db, delete) == [FAKE_URL]
    assert (add.kind, add.photos_before, add.photos_after) == (CHANGE_ADD, 1, 2)
    assert _removed_urls(db, add) == []


def test_refused_edit_is_not_recorded(auth_client, db):
    """Отказ (работа проверена) ничего не пишет."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, photos=1)
    assert _upload(client, block.id).status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    submission.score = 70
    db.commit()

    assert _upload(client, block.id, replace=True, url=NEW_URL).status_code == 409
    assert _changes(db, submission) == []


def test_review_card_shows_history_to_staff_only(auth_client, db, session_factory, admin_user):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, photos=1)
    assert _upload(client, block.id).status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)

    staff_url = f"/cabinet/staff/task-block-submissions/{submission.id}/feedback"
    student_url = f"/cabinet/task-block-submissions/{submission.id}/feedback"
    assert "История изменений" not in client.get(student_url).text

    assert _upload(client, block.id, replace=True, url=NEW_URL).status_code == 200
    assert _upload(client, block.id, replace=True, url=NEW_URL).status_code == 200
    student_page = client.get(student_url)
    assert student_page.status_code == 200
    assert "История изменений" not in student_page.text

    client.cookies.set("session_id", session_factory(admin_user).id)
    page = client.get(staff_url)

    assert page.status_code == 200, page.text
    assert "Сдано впервые" in page.text
    assert "После сдачи работу меняли 2 раза." in page.text
    assert "История изменений" in page.text
    assert "замена фото: было 1, стало 1" in page.text
    assert FAKE_URL in page.text


def test_review_card_without_changes_stays_as_before(auth_client, db, session_factory, admin_user):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, photos=1)
    assert _upload(client, block.id).status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)

    client.cookies.set("session_id", session_factory(admin_user).id)
    page = client.get(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback")

    assert page.status_code == 200
    assert "История изменений" not in page.text
    assert "работу меняли" not in page.text
