"""«Сколько фото сдать» — ровно N фото в сдаче (владелец 02.10.2026).

«Нужно, чтобы они загружали 1 фото только, где сразу 2 эскиза.» Дети слали в
контрольную два-три снимка, и блок закрывался с первого же файла. Теперь у
блока сдачи (контрольная на время, «Домашнее задание») есть число: сервер
не примет больше, работа сдана только при N из N, заменить фото можно только
все разом. Пустое число — прежнее поведение. План —
`plans/2026-10-02-apparchi-photo-count-setting.md`.
"""
from datetime import timedelta, timezone
from unittest.mock import patch

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_PHOTO_UPLOAD,
    BLOCK_TEXT,
    BLOCK_TIMED,
    MAX_SUBMISSION_IMAGES,
    TaskBlock,
    TaskBlockSubmissionImage,
)
from app.services import s3 as s3_service
from app.services.program import day_bounds
from app.services.review_aggregate import DOMAIN_BLOCK_WORK, student_review_items
from app.services.task_blocks import get_state, get_submission, sync_blocks
from app.services.tracker import copy_task_blocks, create_task
from app.services.tz import msk_midnight, today_msk

FAKE_URL = "https://s3.example.com/zadaniya/work.jpg"
TODAY = today_msk()


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
        db, title="Контрольная", user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY + timedelta(days=2))[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, *, required=None, block_type=BLOCK_PHOTO_UPLOAD):
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title="Сдайте работу",
        sort_order=1, is_required=True, required_photos=required,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _files(n):
    return [("photos", (f"w{i}.jpg", b"fake-bytes", "image/jpeg")) for i in range(n)]


def _post(client, block_id, n=1, *, replace=False, comment=""):
    data = {"comment": comment}
    if replace:
        data["replace"] = "1"
    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        return client.post(
            f"/cabinet/tracker/blocks/{block_id}/upload", files=_files(n), data=data,
        )


def _image_count(db, submission):
    return (
        db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id == submission.id)
        .count()
    )


def _is_closed(db, block, user):
    state = get_state(db, block_id=block.id, user_id=user.id)
    return bool(state and state.completed_at)


def test_returned_work_replaces_wrong_photo_after_deadline(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task)
    assert _post(client, block.id).status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    old_image = db.query(TaskBlockSubmissionImage).filter_by(submission_id=submission.id).one()
    task.submit_until = day_bounds(TODAY - timedelta(days=1))[0]
    submission.needs_revision = True
    submission.review_comment = "Загрузи нужный лист"
    db.commit()

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["needs_revision"] is True
    assert payload["edit_reason"] is None
    old_path = old_image.image_s3_path
    db.expunge(old_image)  # SQLite может переиспользовать id удалённой строки.
    with patch("app.api.cabinet_tracker.task_is_archived_for_user", return_value=True), \
         patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        retry = client.post(
            f"/cabinet/tracker/blocks/{block.id}/upload",
            files=[("photos", ("new.jpg", b"new-bytes", "image/jpeg"))],
            data={"replace": "1"},
        )
    assert retry.status_code == 200

    db.refresh(submission)
    assert submission.needs_revision is False
    assert submission.review_comment is None
    images = db.query(TaskBlockSubmissionImage).filter_by(submission_id=submission.id).all()
    assert len(images) == 1
    assert images[0].image_s3_path != old_path


# ── приём ───────────────────────────────────────────────────────────────────

def test_more_than_required_is_refused_and_nothing_is_stored(auth_client, db):
    client, user = auth_client
    block = _block(db, _task(db, user), required=1)

    resp = _post(client, block.id, n=2)

    assert resp.status_code == 422
    assert "ровно 1 фото" in resp.json()["error"]
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert submission is None or _image_count(db, submission) == 0
    assert not _is_closed(db, block, user)


def test_exactly_required_submits_and_closes_the_block(auth_client, db):
    client, user = auth_client
    block = _block(db, _task(db, user), required=1)

    resp = _post(client, block.id, n=1)

    assert resp.status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert submission.submitted_at is not None
    assert _is_closed(db, block, user)


def test_partial_upload_is_not_a_submission_until_n_of_n(auth_client, db):
    """Обрыв на телефоне не запирает: догружать можно частями, но работа сдана
    и видна проверяющему только при N из N."""
    client, user = auth_client
    block = _block(db, _task(db, user), required=3)

    assert _post(client, block.id, n=2, comment="Эскизы").status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert submission.submitted_at is None
    assert submission.comment == "Эскизы"
    assert not _is_closed(db, block, user)
    items = student_review_items(db, student_id=user.id, role_rank=5)
    assert not [i for i in items if i.domain == DOMAIN_BLOCK_WORK]

    too_many = _post(client, block.id, n=2)
    assert too_many.status_code == 422
    assert "Догрузи ещё 1" in too_many.json()["error"]

    assert _post(client, block.id, n=1).status_code == 200
    db.refresh(submission)
    assert submission.submitted_at is not None
    assert _is_closed(db, block, user)
    items = student_review_items(db, student_id=user.id, role_rank=5)
    assert len([i for i in items if i.domain == DOMAIN_BLOCK_WORK]) == 1


def test_after_n_of_n_more_photos_are_refused(auth_client, db):
    client, user = auth_client
    block = _block(db, _task(db, user), required=1)
    _post(client, block.id, n=1)

    resp = _post(client, block.id, n=1)

    assert resp.status_code == 422
    assert "Заменить фото" in resp.json()["error"]


def test_without_a_number_the_old_rule_stays(auth_client, db):
    """Пустое поле — до MAX_SUBMISSION_IMAGES и сдано с первого фото."""
    client, user = auth_client
    block = _block(db, _task(db, user), required=None)

    assert _post(client, block.id, n=2).status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert submission.submitted_at is not None
    assert _is_closed(db, block, user)
    assert _post(client, block.id, n=MAX_SUBMISSION_IMAGES - 2).status_code == 200
    assert _post(client, block.id, n=1).status_code == 422


def test_timed_block_takes_the_number_too(auth_client, db):
    client, user = auth_client
    block = _block(db, _task(db, user), required=1, block_type=BLOCK_TIMED)

    assert _post(client, block.id, n=2).status_code == 422
    assert _post(client, block.id, n=1).status_code == 200


# ── замена ──────────────────────────────────────────────────────────────────

def test_replace_swaps_all_photos_at_once(auth_client, db):
    client, user = auth_client
    block = _block(db, _task(db, user), required=1)
    _post(client, block.id, n=1)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    new_url = "https://s3.example.com/zadaniya/new.jpg"

    with patch.object(s3_service, "upload_to_s3", return_value=new_url):
        resp = client.post(
            f"/cabinet/tracker/blocks/{block.id}/upload",
            files=_files(1), data={"comment": "", "replace": "1"},
        )

    assert resp.status_code == 200
    urls = [
        i.image_s3_url for i in db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id == submission.id)
    ]
    assert urls == [new_url]
    db.refresh(submission)
    assert submission.submitted_at is not None


def test_replace_with_a_wrong_number_keeps_the_old_work(auth_client, db):
    client, user = auth_client
    block = _block(db, _task(db, user), required=1)
    _post(client, block.id, n=1)
    submission = get_submission(db, block_id=block.id, user_id=user.id)

    resp = _post(client, block.id, n=2, replace=True)

    assert resp.status_code == 422
    assert _image_count(db, submission) == 1


def test_single_photo_delete_is_closed_when_number_is_set(auth_client, db):
    client, user = auth_client
    block = _block(db, _task(db, user), required=2)
    _post(client, block.id, n=2)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    image = (
        db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id == submission.id)
        .first()
    )

    resp = client.post(f"/cabinet/tracker/blocks/{block.id}/images/{image.id}/delete")

    assert resp.status_code == 409
    assert _image_count(db, submission) == 2


# ── настройка ───────────────────────────────────────────────────────────────

def test_number_is_kept_only_on_submission_blocks(db, regular_user):
    task = _task(db, regular_user)

    blocks = sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_TIMED, "title": "Контрольная", "required_photos": 1},
        {"block_type": BLOCK_TEXT, "body": "Абзац", "required_photos": 3},
    ])
    db.commit()

    assert [b.required_photos for b in blocks] == [1, None]


def test_week_copy_keeps_the_number_and_the_timer(db, regular_user):
    source = _task(db, regular_user)
    target = _task(db, regular_user)
    db.add(TaskBlock(
        task_id=source.id, block_type=BLOCK_TIMED, title="Контрольная",
        sort_order=1, required_photos=1, time_limit_minutes=90,
    ))
    db.commit()

    copy_task_blocks(db, from_task_id=source.id, to_task_id=target.id)
    db.commit()

    clone = db.query(TaskBlock).filter(TaskBlock.task_id == target.id).one()
    assert clone.required_photos == 1
    assert clone.time_limit_minutes == 90


def test_feed_tells_the_student_the_number(auth_client, db):
    """Экран ученика получает число и лимит — без них JS не напишет «ровно N»."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, required=1)

    from app.api.cabinet_tracker import _submission_payload  # noqa: PLC0415

    payload = _submission_payload(db, task, block, user_id=user.id)

    assert payload["required_photos"] == 1
    assert payload["max_files"] == 1
