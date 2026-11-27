"""Новая проверка только для ГП, старая форма доступна кураторам."""
from datetime import datetime, timedelta, timezone
from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app.models.learning_topic import LearningTopic
from app.models.task_block import BLOCK_TIMED, TaskBlock, TaskBlockSubmission, TaskBlockSubmissionImage
from app.models.task_block_feedback import TaskBlockFeedbackMessage
from app.services.review_aggregate import unreviewed_counts_by_student
from app.services.task_blocks import control_review_mode, mark_submitted
from app.services.tz import MSK_TZ
from tests.test_feedback_rating import _setup_block, _login, _png, _send, _quiet_side_effects


@pytest.mark.parametrize("role", ["куратор", "суперадмин", "модератор"])
def test_demo_rejects_other_roles(db, user_factory, session_factory, client, role):
    curator, _, _, submission = _setup_block(db, user_factory, base=998_101)
    user = curator if role == "куратор" else user_factory(vk_id=998_111, role_name=role)
    _login(client, session_factory, user)
    queue = client.get("/cabinet/staff/students-review/queue", follow_redirects=False)
    assert queue.status_code in (302, 403)
    if queue.status_code == 302:
        assert queue.headers["location"] == "/cabinet/students"
    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    assert client.get(base + "/feedback?review_demo=1").status_code == 403
    assert client.put(base + "/draft", json={"text": "Закрыто", "cursor": 0}).status_code == 403
    assert client.post(base + "/send-feedback?preview=true").status_code == 403


def test_curator_keeps_old_form_and_send(db, user_factory, session_factory, client):
    curator, student, _, submission = _setup_block(db, user_factory, base=998_201)
    _login(client, session_factory, curator)
    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    page = client.get(base + "/feedback")
    assert page.status_code == 200
    assert "data-review-draft-url=" not in page.text
    assert 'id="review-section"' not in page.text
    assert 'data-review-preview aria' not in page.text
    assert "Прошлые работы" not in page.text
    assert 'href="/cabinet/staff/students-review/queue"' not in page.text
    assert client.post(base + "/messages", data={"text": "Старая проверка"}).status_code == 200
    assert _send(client, submission).status_code == 200
    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert "Старая проверка" in page.text
    assert not db.query(TaskBlockFeedbackMessage).one().is_draft


def test_head_can_test_queue_and_editor(db, user_factory, session_factory, client):
    curator, _, task, submission = _setup_block(db, user_factory, base=998_301)
    task.subject = "Рисунок"
    db.get(LearningTopic, task.topic_id).is_published = True
    chief = user_factory(vk_id=998_311, role_name="админ")
    photo_url = "https://s3.test/student-drawing.jpg"
    db.add(TaskBlockSubmissionImage(submission_id=submission.id, image_s3_url=photo_url))
    db.commit()
    _login(client, session_factory, chief)
    queue = client.get("/cabinet/staff/students-review/queue", params={
        "review_curator": curator.id, "cycle_id": task.topic_id, "status": "all",
    })
    assert queue.status_code == 200
    assert "Проверка работ · демо" in queue.text
    assert '<ul class="review-student-list">' in queue.text
    assert photo_url not in queue.text
    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    assert base + "/feedback?" in queue.text
    page = client.get(base + "/feedback?review_demo=1")
    assert page.status_code == 200
    assert photo_url in page.text
    assert "Привязать замечание" not in page.text
    assert all(f'id="review-{field}"' not in page.text for field in ("section", "work", "image"))
    assert "data-review-draft-url=" in page.text
    assert 'data-demo-record="note"' in page.text
    assert 'data-demo-record="voice"' in page.text
    assert "Сохранить в черновики" not in page.text
    assert page.text.index("Сохранить оценку") < page.text.index("Задание ·")
    assert page.text.index("Задание ·") < page.text.index("Сообщение ученику")
    assert page.text.index("Сообщение ученику") < page.text.index("Прошлые работы")
    curator_view = client.get(base + f"/feedback?review_demo=1&review_curator={curator.id}")
    assert curator_view.status_code == 200
    assert 'data-demo-file="video"' in curator_view.text
    assert "data-demo-record=" not in curator_view.text
    assert 'data-demo-file="audio"' not in curator_view.text
    assert client.put(base + "/draft", json={"text": "Демо ГП", "cursor": 0}).status_code == 200


def test_demo_attachment_requires_role_and_current_cursor(db, user_factory, session_factory, client, monkeypatch):
    curator, student, _, submission = _setup_block(db, user_factory, base=998_601, block_type=BLOCK_TIMED)
    chief = user_factory(vk_id=998_611, role_name="админ")
    _login(client, session_factory, chief)
    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    curator_upload = base + f"/messages?review_demo=true&review_curator={curator.id}"
    assert client.post(curator_upload, data={"demo_cursor": 0, "video_note": "1"}).status_code == 403
    assert client.post(
        curator_upload, data={"demo_cursor": 0}, files={"audio": ("voice.ogg", b"voice", "audio/ogg")},
    ).status_code == 403

    async def upload_photo(*args):
        return "demo/photo.png", "https://s3.test/demo/photo.png"

    monkeypatch.setattr("app.services.task_block_feedback._upload_photo", upload_photo)
    response = client.post(curator_upload, data={"demo_cursor": 0}, files={"photo": _png()})
    assert response.status_code == 200, response.text
    cursor = response.json()["cursor"]
    assert client.post(curator_upload, data={"demo_cursor": 0}, files={"photo": _png()}).status_code == 409
    message = db.get(TaskBlockFeedbackMessage, cursor)
    assert message.is_draft
    assert message.photo_s3_url == "https://s3.test/demo/photo.png"

    _login(client, session_factory, student)
    student_page = f"/cabinet/task-block-submissions/{submission.id}/feedback"
    assert message.photo_s3_url not in client.get(student_page).text
    _login(client, session_factory, chief)
    preview = client.post(base + "/send-feedback?preview=true")
    params = {"snapshot": preview.json()["snapshot"]}
    db.refresh(submission)
    assert submission.reviewed_at is None
    assert unreviewed_counts_by_student(db, curator_id=curator.id) == {student.id: 1}
    assert client.post(base + "/send-feedback", params=params).status_code == 200
    assert client.post(base + "/send-feedback", params=params).status_code == 409
    db.refresh(submission)
    assert submission.reviewed_at is None
    assert unreviewed_counts_by_student(db, curator_id=curator.id) == {student.id: 1}
    assert client.post(base + "/score", json={"score": 85}).status_code == 200
    db.refresh(submission)
    assert submission.reviewed_at is not None
    assert submission.reviewed_by_id == chief.id
    assert not unreviewed_counts_by_student(db, curator_id=curator.id)
    assert "<strong>Проверено</strong>" in client.get(base + "/feedback?review_demo=1").text
    _login(client, session_factory, student)
    assert message.photo_s3_url in client.get(student_page).text


def test_impersonation_requires_head(db, user_factory, session_factory, client):
    curator, _, _, submission = _setup_block(db, user_factory, base=998_401)
    chief = user_factory(vk_id=998_411, role_name="админ")
    boss = user_factory(vk_id=998_412, role_name="суперадмин")
    url = f"/cabinet/staff/task-block-submissions/{submission.id}/feedback?review_demo=1"
    for original, expected in ((chief, 200), (boss, 403)):
        session = session_factory(curator)
        session.impersonated_by_id = original.id
        db.commit()
        client.cookies.clear()
        client.cookies.set("session_id", session.id)
        assert client.get(url).status_code == expected


@pytest.mark.parametrize("is_liza", [True, False])
def test_superadmin_viewing_head_can_open_only_liza_demo(
    db, user_factory, session_factory, client, monkeypatch, is_liza,
):
    _, _, _, submission = _setup_block(db, user_factory, base=998_501)
    chief = user_factory(vk_id=998_511, role_name="админ")
    boss = user_factory(vk_id=998_512, role_name="суперадмин")
    monkeypatch.setattr("app.config.settings.view_origin", str(client.base_url))
    monkeypatch.setattr(
        "app.services.curator_review_demo.LIZA_HEAD_USER_ID",
        chief.id if is_liza else chief.id + 1000,
    )
    session = session_factory(chief)
    session.impersonated_by_id = boss.id
    db.commit()
    client.cookies.set("session_id", session.id)
    url = f"/cabinet/staff/task-block-submissions/{submission.id}"
    page = client.get("/cabinet/staff/students-review")
    assert page.status_code == 200
    assert ("Новая проверка · демо" in page.text) == is_liza
    queue = client.get("/cabinet/staff/students-review/queue?review_demo=1", follow_redirects=False)
    assert queue.status_code == (200 if is_liza else 302)
    expected = 200 if is_liza else 403
    assert client.get(url + "/feedback?review_demo=1").status_code == expected
    assert client.put(url + "/draft", json={"text": "Тест", "cursor": 0}).status_code == expected
    assert client.post(url + "/send-feedback?preview=true").status_code == expected


@pytest.mark.parametrize("view_as_alena", [False, True])
def test_alena_demo_is_limited_to_romans_assigned_submission(
    db, user_factory, session_factory, client, monkeypatch, view_as_alena,
):
    alena, roman, _, submission = _setup_block(db, user_factory, base=998_631)
    other_curator, other_student, _, other_submission = _setup_block(db, user_factory, base=998_641)
    other_student.curator_id = alena.id
    db.commit()
    prefix = "app.services.curator_review_demo."
    monkeypatch.setattr(prefix + "ALENA_CURATOR_USER_ID", alena.id)
    monkeypatch.setattr(prefix + "ROMAN_DEMO_STUDENT_ID", roman.id)
    monkeypatch.setattr(prefix + "ROMAN_DRAWING_CONTROL_SUBMISSION_ID", submission.id)

    if view_as_alena:
        admin = user_factory(vk_id=998_651, role_name="суперадмин")
        monkeypatch.setattr("app.config.settings.view_origin", str(client.base_url))
        session = session_factory(alena)
        session.impersonated_by_id = admin.id
        db.commit()
        client.cookies.set("session_id", session.id)
    else:
        _login(client, session_factory, alena)

    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    for suffix in ("", "?review_demo=1"):
        page = client.get(base + "/feedback" + suffix)
        assert page.status_code == 200
        assert "data-review-draft-url=" in page.text
        assert "← Очередь проверки" not in page.text
    assert "Новая проверка · демо" not in client.get("/cabinet/staff/students-review").text
    queue = client.get("/cabinet/staff/students-review/queue?review_demo=1", follow_redirects=False)
    assert queue.status_code in (302, 403)

    draft = {"text": "Замечание по пристройке", "cursor": 0}
    assert client.put(base + "/draft", json=draft).status_code == 200
    preview = client.post(base + "/send-feedback?preview=true")
    assert preview.status_code == 200
    assert draft["text"] in preview.text
    assert db.query(TaskBlockFeedbackMessage).one().is_draft
    # Без вложения форма отвечает 422 только после проверки демо-доступа.
    assert client.post(base + "/messages?review_demo=true", data={"demo_cursor": 0}).status_code == 422

    other_base = f"/cabinet/staff/task-block-submissions/{other_submission.id}"
    other_page = client.get(other_base + "/feedback")
    assert other_page.status_code == 200
    assert "data-review-draft-url=" not in other_page.text
    assert client.get(other_base + "/feedback?review_demo=1").status_code == 403
    assert client.put(other_base + "/draft", json=draft).status_code == 403
    assert client.post(other_base + "/send-feedback?preview=true").status_code == 403

    roman.curator_id = other_curator.id
    db.commit()
    assert client.get(base + "/feedback?review_demo=1").status_code == 403
    assert client.put(base + "/draft", json=draft).status_code == 403


def test_control_deadlines_are_inclusive_and_mode_is_frozen(db, user_factory, monkeypatch):
    _, _, _, submission = _setup_block(db, user_factory, base=998_701, block_type=BLOCK_TIMED)
    block = db.get(TaskBlock, submission.block_id)
    first = datetime(2026, 11, 27, 23, 59, tzinfo=MSK_TZ)
    second = datetime(2026, 11, 28, 19, tzinfo=MSK_TZ)
    block.submit_until = first
    assert control_review_mode(block, first) == "full"
    assert control_review_mode(block, first + timedelta(seconds=1)) == "text"
    assert control_review_mode(block, second) == "text"
    assert control_review_mode(block, second + timedelta(seconds=1)) == "score"
    submission.submitted_at = None
    monkeypatch.setattr("app.services.task_blocks._now", lambda: first)
    mark_submitted(db, submission=submission)
    monkeypatch.setattr("app.services.task_blocks._now", lambda: second + timedelta(days=1))
    mark_submitted(db, submission=submission)
    assert submission.control_review_mode == "full"


def test_control_uses_blocks_tariff_deadline(db, user_factory, monkeypatch):
    from app.models.task_block import TaskBlockTariffDeadline
    from app.services.task_blocks import submission_control_review_mode, submission_review_queue

    _, student, _, submission = _setup_block(db, user_factory, base=998_721, block_type=BLOCK_TIMED)
    block = db.get(TaskBlock, submission.block_id)
    deadline = datetime(2026, 11, 27, 19, tzinfo=MSK_TZ)
    block.submit_until = deadline - timedelta(days=3)
    db.add(TaskBlockTariffDeadline(block_id=block.id, tariff=student.tariff, submit_until=deadline))
    submitted_at = deadline - timedelta(minutes=1)
    submission.submitted_at = submitted_at
    db.commit()

    assert submission_control_review_mode(db, submission) == "full"
    assert submission_review_queue(db, student_id=student.id)[0]["control_review_mode"] == "full"
    submission.submitted_at = None
    monkeypatch.setattr("app.services.task_blocks._now", lambda: submitted_at)
    mark_submitted(db, submission=submission)
    assert submission.control_review_mode == "full"


@pytest.mark.parametrize("unlimited_override", [False, True])
def test_control_without_block_cutoff_keeps_full_review(db, user_factory, session_factory, client, monkeypatch, unlimited_override):
    from app.models.task_block import TaskBlockTariffDeadline
    from app.services.task_blocks import submission_control_review_mode, submission_review_queue

    _, student, task, submission = _setup_block(db, user_factory, base=998_741, block_type=BLOCK_TIMED)
    block = db.get(TaskBlock, submission.block_id)
    submitted_at = datetime(2026, 11, 27, 19, tzinfo=MSK_TZ)
    task.submit_until = submitted_at - timedelta(days=5)
    submission.submitted_at = submitted_at
    override = None
    if unlimited_override:
        block.submit_until = task.submit_until
        override = TaskBlockTariffDeadline(block_id=block.id, tariff=student.tariff, submit_until=None)
        db.add(override)
    db.commit()

    assert submission_review_queue(db, student_id=student.id)[0]["control_review_mode"] is None
    chief = user_factory(vk_id=998_751, role_name="админ")
    _login(client, session_factory, chief)
    url = f"/cabinet/staff/task-block-submissions/{submission.id}"
    assert client.post(url + "/messages", data={"text": "Разбор"}).status_code == 200
    assert client.post(url + "/score", json={"score": 80}).status_code == 200

    db.refresh(submission)
    submission.submitted_at = None
    monkeypatch.setattr("app.services.task_blocks._now", lambda: submitted_at)
    mark_submitted(db, submission=submission)
    assert submission.control_review_mode == "full"

    block.submit_until = task.submit_until
    if override is not None:
        db.delete(override)
    db.commit()
    assert submission_control_review_mode(db, submission) == "full"


def test_timed_upload_uses_container_deadline_before_task_due_date():
    from app.models.tracker import TrackerTask
    from app.services.submission_edit import upload_deadline

    deadline = datetime(2026, 11, 27, 19, tzinfo=MSK_TZ)
    task = TrackerTask(due_at=deadline - timedelta(days=1))
    block = TaskBlock(block_type=BLOCK_TIMED, container_path=[{"key": "control", "submit_until": "2026-11-27T19:00"}])
    assert upload_deadline(task, block) == deadline


def test_control_mode_backfill_preserves_first_completion_and_existing_modes(db, user_factory):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.models.task_block import TaskBlockState, TaskBlockTariffDeadline

    _, first_student, _, first = _setup_block(db, user_factory, base=998_761, block_type=BLOCK_TIMED)
    block = db.get(TaskBlock, first.block_id)
    deadline = datetime(2026, 11, 27, 23, 59, tzinfo=MSK_TZ).astimezone(timezone.utc)
    second = datetime(2026, 11, 28, 19, tzinfo=MSK_TZ).astimezone(timezone.utc)
    block.submit_until = deadline
    first.submitted_at = second + timedelta(hours=1)
    db.add(TaskBlockState(block_id=block.id, user_id=first_student.id, status="done", completed_at=deadline))
    cases = [
        # Tariff, submission timestamp, existing classification, expected result.
        ("B", second, None, "text"),
        ("B", second + timedelta(seconds=1), None, "score"),
        ("C", second + timedelta(days=1), None, "full"),
        ("D", second, None, "full"),
        ("B", second, "score", "score"),
        ("B", None, None, None),
    ]
    rows = [(first, "full")]
    for index, (tariff, submitted_at, existing, expected) in enumerate(cases, 10):
        student = user_factory(vk_id=998_760 + index, tariff=tariff)
        submission = TaskBlockSubmission(
            block_id=block.id, user_id=student.id, submitted_at=submitted_at, control_review_mode=existing,
        )
        db.add(submission)
        rows.append((submission, expected))
    db.add_all([
        TaskBlockTariffDeadline(block_id=block.id, tariff="C", submit_until=None),
        TaskBlockTariffDeadline(block_id=block.id, tariff="D", submit_until=second),
    ])
    db.flush()
    source = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "9d8fe4b2c1a7_backfill_control_review_mode.py"
    spec = spec_from_file_location("control_mode_backfill", source)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(db.connection()))
    migration.upgrade()
    block.submit_until = deadline - timedelta(days=10)
    db.flush()
    migration.upgrade()

    for submission, expected in rows:
        db.refresh(submission)
        assert submission.control_review_mode == expected


@pytest.mark.parametrize("score_first", [True, False])
@pytest.mark.parametrize("mode", [None, "text"])
def test_control_requires_feedback_and_score(db, user_factory, session_factory, client, score_first, mode):
    _, _, _, submission = _setup_block(db, user_factory, base=998_801, block_type=BLOCK_TIMED)
    chief = user_factory(vk_id=998_811, role_name="админ")
    submission.control_review_mode = mode
    db.commit()
    _login(client, session_factory, chief)
    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    page_url = base + "/feedback?review_demo=1"
    revision_url = base + "/revision"
    page = client.get(page_url)
    assert page.status_code == 200
    assert 'data-review-toggle\n' not in page.text
    assert 'data-return-toggle>' not in page.text
    assert client.post(
        f"/cabinet/staff/students-review/block-work/{submission.id}/reviewed", json={"reviewed": True},
    ).status_code == 409
    if mode == "text":
        assert client.post(base + "/messages", files={"photo": _png()}, data={"text": "Разбор"}).status_code == 422

    def score():
        assert client.post(base + "/score", json={"score": 0}).status_code == 200

    def send():
        assert client.post(base + "/messages", data={"text": "Разбор"}).status_code == 200
        assert client.post(base + "/send-feedback").status_code == 200

    one, two = (score, send) if score_first else (send, score)
    one()
    db.refresh(submission)
    assert submission.reviewed_at is None
    page = client.get(page_url)
    assert ("Ждёт ОС" if score_first else "Ждём баллы") in page.text
    assert 'data-return-toggle>' not in page.text
    assert client.post(revision_url, data={"comment": "Доделай тени"}).status_code == 409
    two()
    db.refresh(submission)
    assert submission.reviewed_at is not None
    page = client.get(page_url)
    assert "<strong>Проверено</strong>" in page.text
    assert page.text.index('data-review-preview-send>') < page.text.index('data-return-toggle>')
    assert client.post(revision_url, data={"comment": "Доделай тени"}).status_code == 200
    db.refresh(submission)
    assert submission.needs_revision and submission.reviewed_at is None


def test_only_liza_can_score_a_late_control(db, user_factory, session_factory, client, monkeypatch):
    _, _, _, submission = _setup_block(db, user_factory, base=998_901, block_type=BLOCK_TIMED)
    chief = user_factory(vk_id=998_911, role_name="админ")
    liza = user_factory(vk_id=998_912, role_name="админ")
    monkeypatch.setattr("app.services.curator_review_demo.LIZA_HEAD_USER_ID", liza.id)
    submission.control_review_mode = "score"
    db.commit()
    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    _login(client, session_factory, chief)
    assert client.post(base + "/score", json={"score": 99}).status_code == 403
    _login(client, session_factory, liza)
    assert client.post(base + "/messages", data={"text": "Разбор"}).status_code == 403
    assert client.post(base + "/score", json={"score": 99}).status_code == 200
    db.refresh(submission)
    assert submission.reviewed_at is not None


def test_all_student_control_filters_partition_assigned_students(db, user_factory, session_factory, client, monkeypatch):
    curator, first_student, task, submission = _setup_block(db, user_factory, base=999_001, block_type=BLOCK_TIMED)
    liza = user_factory(vk_id=999_003, role_name="админ")
    other_head = user_factory(vk_id=999_004, role_name="админ")
    monkeypatch.setattr("app.services.curator_review_demo.LIZA_HEAD_USER_ID", liza.id)
    topic = db.get(LearningTopic, task.topic_id)
    topic.opens_at = datetime.now(timezone.utc) - timedelta(days=5)
    topic.ends_at = datetime.now(timezone.utc) + timedelta(days=1)
    topic.assign_to_all = True
    topic.is_published = True
    task.subject = "Композиция"
    block = db.get(TaskBlock, submission.block_id)
    block.submit_until = datetime.now(timezone.utc) - timedelta(days=2)
    first_student.is_group_member = True
    first_student.name = "Ученик вовремя"
    submission.control_review_mode = "full"
    states = {"full": first_student.name, "text": "Ученик между сроками", "score": "Ученик после сроков", "missing": "Ученик без сдачи"}
    for number, mode in enumerate(("text", "score", "missing"), 10):
        student = user_factory(vk_id=999_000 + number, name=states[mode])
        student.is_group_member = True
        student.curator_id = curator.id
        if mode != "missing":
            db.add(TaskBlockSubmission(
                block_id=block.id, user_id=student.id,
                submitted_at=datetime.now(timezone.utc), control_review_mode=mode,
            ))
    db.commit()
    _login(client, session_factory, liza)
    url = "/cabinet/staff/students-review/queue"
    params = {"review_curator": "all", "cycle_id": topic.id, "subject": task.subject}
    for mode, expected in states.items():
        result = client.get(url, params={**params, "status": mode})
        assert result.status_code == 200
        assert expected in result.text
        assert all(name not in result.text for key, name in states.items() if key != mode)
    _login(client, session_factory, other_head)
    result = client.get(url, params={**params, "status": "score"})
    assert result.status_code == 200
    assert states["score"] not in result.text
