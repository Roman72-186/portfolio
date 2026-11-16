"""Хранение замечания в черновике до явной отправки обратной связи."""

from datetime import datetime, timedelta, timezone

from app.dependencies import require_csrf_header
from app.models.notification import Notification
from app.models.section_access import SectionAccessRule
from app.models.task_block import TaskBlock, TaskBlockSubmissionImage
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.services.linked_feedback import sync_links
from tests.test_feedback_rating import (
    _login,
    _quiet_side_effects,  # noqa: F401 — проверка диалога без доставки уведомлений
    _send,
    _setup_block,
)
from tests.test_linked_feedback import _submit, _task_with_block


def _save(client, submission, text, *, cursor=0, draft=None):
    return client.put(
        f"/cabinet/staff/task-block-submissions/{submission.id}/draft",
        json={"text": text, "cursor": cursor, **({"message_id": draft["id"], "revision": draft["revision"]} if draft else {})},
    )


def test_autosave_retries_updates_and_delivers_only_on_send(db, user_factory, session_factory, client):
    curator, student, _, submission = _setup_block(db, user_factory, base=995_001)
    _login(client, session_factory, curator)
    created = _save(client, submission, "Первое замечание\n")
    assert created.status_code == 200, created.text
    draft = created.json()
    assert _save(client, submission, "Первое замечание\n").json() == draft
    assert _save(client, submission, "Черновик второй вкладки").status_code == 409
    changed = _save(client, submission, "Исправленное замечание", cursor=draft["cursor"], draft=draft).json()
    assert changed["id"] == draft["id"]
    assert changed["revision"] != draft["revision"]
    assert _save(client, submission, "Исправленное замечание", cursor=draft["cursor"], draft=draft).json() == changed
    assert _save(client, submission, "Старая версия", cursor=draft["cursor"], draft=draft).status_code == 409
    assert db.query(TaskBlockFeedbackMessage).count() == 1
    assert db.query(Notification).filter(Notification.user_id == student.id).count() == 0

    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert "Исправленное замечание" not in page.text
    _login(client, session_factory, curator)
    assert _send(client, submission).status_code == 200
    assert _save(client, submission, "Исправленное замечание").status_code == 409
    assert _save(client, submission, "Слишком поздно", cursor=changed["cursor"], draft=changed).status_code == 409
    assert db.query(TaskBlockFeedbackMessage).count() == 1
    assert db.query(Notification).filter(Notification.user_id == student.id).count() == 1
    _login(client, session_factory, student)
    assert "Исправленное замечание" in client.get(
        f"/cabinet/task-block-submissions/{submission.id}/feedback",
    ).text


def test_autosave_clears_only_its_draft_and_does_not_create_empty_dialog(db, user_factory, session_factory, client):
    curator, _, _, submission = _setup_block(db, user_factory, base=995_011)
    _login(client, session_factory, curator)
    assert _save(client, submission, " \n").json() == {"ok": True, "id": None, "revision": None, "cursor": 0}
    assert db.query(TaskBlockFeedback).count() == 0
    assert client.post(
        f"/cabinet/staff/task-block-submissions/{submission.id}/messages", data={"text": "Другой черновик"},
    ).status_code == 200
    first = db.query(TaskBlockFeedbackMessage).one()
    created = _save(client, submission, "Очистить", cursor=first.id).json()
    cleared = _save(client, submission, "", cursor=created["cursor"], draft=created)
    assert cleared.json() == {"ok": True, "id": None, "revision": None, "cursor": first.id}
    assert _save(client, submission, "", cursor=created["cursor"], draft=created).json() == cleared.json()
    assert [row.text for row in db.query(TaskBlockFeedbackMessage).all()] == ["Другой черновик"]


def test_photo_reference_survives_sending_and_removal(db, user_factory, session_factory, client):
    curator, student, _, submission = _setup_block(db, user_factory, base=995_091)
    _, _, _, foreign = _setup_block(db, user_factory, base=995_093)
    photo = TaskBlockSubmissionImage(submission_id=submission.id, image_s3_url="https://example.test/local.png")
    other_photo = TaskBlockSubmissionImage(submission_id=foreign.id, image_s3_url="https://example.test/foreign.png")
    db.add_all([photo, other_photo])
    db.commit()
    _login(client, session_factory, curator)
    page_url = f"/cabinet/staff/task-block-submissions/{submission.id}/feedback"
    draft_url = f"/cabinet/staff/task-block-submissions/{submission.id}/draft"
    student_url = f"/cabinet/task-block-submissions/{submission.id}/feedback"
    page = client.get(page_url)
    assert page.status_code == 200
    assert "Прошлые работы" in page.text and "Портфолио ученика" in page.text
    payload = {"text": "Замечание к фото", "cursor": 0, "image_id": photo.id}
    assert client.put(draft_url, json={**payload, "image_id": other_photo.id}).status_code == 422
    saved = client.put(draft_url, json=payload)
    assert saved.status_code == 200, saved.text
    assert client.put(draft_url, json=payload).json() == saved.json()
    assert db.query(TaskBlockFeedbackMessage).one().review_image_id == photo.id
    _login(client, session_factory, student)
    assert payload["text"] not in client.get(student_url).text
    _login(client, session_factory, curator)
    assert _send(client, submission).status_code == 200
    _login(client, session_factory, student)
    assert payload["text"] in client.get(student_url).text
    db.delete(photo)
    db.commit()
    assert "Фото к замечанию больше недоступно." in client.get(student_url).text


def test_autosave_checks_csrf_and_does_not_allow_foreign_draft(db, user_factory, session_factory, client):
    curator, student, _, submission = _setup_block(db, user_factory, base=995_021)
    other = user_factory(vk_id=995_023, name="Другой куратор", role_name="куратор")
    chief = user_factory(vk_id=995_024, name="Главный преподаватель", role_name="админ")
    _login(client, session_factory, curator)
    draft = _save(client, submission, "Своё замечание").json()
    _login(client, session_factory, other)
    assert _save(client, submission, "Чужой ученик").status_code == 403
    _login(client, session_factory, student)
    assert _save(client, submission, "Ученик").status_code == 403
    _login(client, session_factory, chief)
    assert _save(client, submission, "Чужой черновик", draft=draft).status_code == 409

    _login(client, session_factory, curator)
    no_op = client.app.dependency_overrides.pop(require_csrf_header)
    try:
        assert _save(client, submission, "Нет токена", draft=draft).status_code == 403
    finally:
        client.app.dependency_overrides[require_csrf_header] = no_op
    db.add(SectionAccessRule(section_key="students_review", user_id=curator.id, level="view"))
    db.commit()
    assert _save(client, submission, "Только чтение", draft=draft).status_code == 403
    assert db.query(TaskBlockFeedbackMessage).one().text == "Своё замечание"


def test_autosave_keeps_linked_work_in_main_dialog(db, user_factory, session_factory, client):
    curator, student, _, first = _setup_block(db, user_factory, base=995_031)
    _, main = _task_with_block(db, "Следующая неделя", db.get(TaskBlock, first.block_id).block_type)
    sync_links(db, main, [first.block_id])
    db.commit()
    _login(client, session_factory, curator)
    assert _save(client, first, "Ранний текст").status_code == 409
    assert db.query(TaskBlockFeedback).count() == 0

    latest = _submit(db, main, student, datetime.now(timezone.utc))
    photo = TaskBlockSubmissionImage(submission_id=first.id, image_s3_url="https://example.test/linked.png")
    db.add(photo)
    db.commit()
    result = client.put(
        f"/cabinet/staff/task-block-submissions/{latest.id}/draft",
        json={"text": "Общий разбор", "cursor": 0, "image_id": photo.id},
    )
    assert result.status_code == 200, result.text
    feedback = db.query(TaskBlockFeedback).one()
    assert feedback.submission_id == latest.id
    assert feedback.messages[0].review_image_id == photo.id


def test_autosave_rejects_late_save_after_dialog_closes(db, user_factory, session_factory, client):
    curator, _, _, submission = _setup_block(db, user_factory, base=995_041)
    _login(client, session_factory, curator)
    draft = _save(client, submission, "Замечание").json()
    assert _send(client, submission).status_code == 200
    feedback = db.query(TaskBlockFeedback).one()
    feedback.feedback_closed_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert _save(client, submission, "Новый черновик", cursor=draft["cursor"]).status_code == 403
    assert db.query(TaskBlockFeedbackMessage).one().text == "Замечание"

def test_section_target_and_preview_snapshot(db, user_factory, session_factory, client):
    curator, student, _, first = _setup_block(db, user_factory, base=995_101)
    _, independent_student, _, foreign = _setup_block(db, user_factory, base=995_111)
    block_type = db.get(TaskBlock, first.block_id).block_type
    _, independent = _task_with_block(db, "Самостоятельная работа", block_type)
    standalone = _submit(db, independent, student, datetime.now(timezone.utc))
    _, main = _task_with_block(db, "Общая проверка", block_type)
    sync_links(db, main, [first.block_id])
    latest = _submit(db, main, student, datetime.now(timezone.utc))
    wrong_photo = TaskBlockSubmissionImage(submission_id=latest.id, image_s3_url="https://example.test/main.png")
    db.add(wrong_photo)
    db.commit()
    base = f"/cabinet/staff/task-block-submissions/{latest.id}"
    payload = {"text": "Замечание к отдельной работе", "cursor": 0,
               "section": "composition", "target_submission_id": first.id}

    _login(client, session_factory, curator)
    for invalid in (
        {"target_submission_id": foreign.id}, {"target_submission_id": standalone.id},
        {"image_id": wrong_photo.id}, {"section": "invalid"},
    ):
        assert client.put(base + "/draft", json={**payload, **invalid}).status_code == 422
    saved = client.put(base + "/draft", json=payload)
    assert saved.status_code == 200, saved.text
    assert client.put(base + "/draft", json=payload).json() == saved.json()
    second = client.put(base + "/draft", json={
        "text": "Второе замечание", "cursor": saved.json()["cursor"], "section": "graphics",
    })
    assert second.status_code == 200, second.text

    assert _save(client, standalone, "Текст самостоятельного диалога").status_code == 200
    assert client.post(base + "/messages", data={"video_link": "https://example.test/video"}).status_code == 200

    preview_url = base + "/send-feedback?preview=true"
    _login(client, session_factory, student)
    assert client.post(preview_url).status_code == 403
    _login(client, session_factory, independent_student)
    assert client.post(preview_url).status_code == 403
    _login(client, session_factory, curator)
    preview = client.post(preview_url).json()
    assert [item["section"] for item in preview["items"]] == [
        "Композиционное решение", "Графическое решение", None,
    ]
    assert preview["items"][2]["attachments"] == ["Ссылка на видео"]
    assert preview["items"][0]["work"]
    assert preview["items"][0]["image"] is None
    assert db.query(Notification).count() == 0

    assert _save(client, latest, "Уточнение", cursor=second.json()["cursor"], draft=saved.json()).status_code == 200
    assert client.post(base + "/send-feedback", params={"snapshot": preview["snapshot"]}).status_code == 409
    assert db.query(TaskBlockFeedbackMessage).filter_by(is_draft=True).count() == 4
    fresh = client.post(preview_url).json()
    assert client.post(base + "/send-feedback", params={"snapshot": fresh["snapshot"]}).status_code == 200
    assert db.query(Notification).filter_by(user_id=student.id).count() == 1
    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{latest.id}/feedback")
    assert "Уточнение" in page.text
    assert "Композиционное решение" in page.text
    assert "Графическое решение" in page.text
    assert "Текст самостоятельного диалога" not in page.text
    assert db.query(TaskBlockFeedbackMessage).filter_by(is_draft=True).one().text == "Текст самостоятельного диалога"
