from datetime import datetime, timedelta, timezone

from app.constants import PROGRAM_HOLIDAY
from app.dependencies import require_csrf
from app.main import app
from app.models.learning_topic import LearningTopic, LearningTopicTag
from app.models.payment import KIND_HOLIDAY, STATUS_PAID, Payment
from app.models.task_block import BLOCK_PHOTO_UPLOAD, BLOCK_TIMED, TaskBlock, TaskBlockSubmission
from app.models.tracker import TrackerTask
from app.services.holiday_review import ASSIGN_PATH, HISTORY_PATH, QUEUE_PATH
from app.services.linked_feedback import sync_links
from app.services.tags import add_tag_to_user
from app.services.video_topics import accessible_topic_ids
from tests.test_feedback_rating import _login, _quiet_side_effects, _send, _staff_says
from tests.test_holiday_intake import CHAT_ID, _callback, _mock_callback
from tests.test_holiday_track_payment import _newcomer, _post_webhook, _track, no_delivery, prodamus


def _week(db, track, start):
    period = LearningTopic(
        title="Информационные продукты", kind="period", program=PROGRAM_HOLIDAY,
        opens_at=start - timedelta(days=10), is_published=True, assign_to_all=True,
    )
    db.add(period)
    db.flush()
    week = LearningTopic(
        title=track.title, kind="week", parent_id=period.id, opens_at=start,
        ends_at=track.access_until, is_published=True, assign_to_all=False,
    )
    db.add(week)
    db.flush()
    db.add(LearningTopicTag(topic_id=week.id, tag_id=track.tag_id))
    db.commit()
    return week


def _submit(db, student, week, title):
    task = TrackerTask(title=title, kind="material", topic_id=week.id, is_published=True)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_PHOTO_UPLOAD, title="Сдача")
    db.add(block)
    db.flush()
    submission = TaskBlockSubmission(
        block_id=block.id, user_id=student.id, submitted_at=datetime.now(timezone.utc),
    )
    db.add(submission)
    db.commit()
    return submission


def test_holiday_group_stays_in_pending_queue_until_linked_control_has_a_score(
    client, db, user_factory, session_factory, rendered,
):
    now = datetime.now(timezone.utc)
    track = _track(db, title="Учебный поток")
    week = _week(db, track, now - timedelta(days=1))
    student = _newcomer(db, user_factory, profile_completed=True)
    add_tag_to_user(db, student.id, track.tag_id)
    first = _submit(db, student, week, "Первая контрольная")
    main = _submit(db, student, week, "Общая проверка")
    first_block, main_block = (db.get(TaskBlock, submission.block_id) for submission in (first, main))
    first_block.block_type = main_block.block_type = BLOCK_TIMED
    first.control_review_mode = main.control_review_mode = "full"
    sync_links(db, main_block, [first_block.id])
    main.score = 90
    main.reviewed_at = now
    db.commit()
    head = user_factory(vk_id=310_008, role_name="админ")
    _login(client, session_factory, head)

    def queue_ids(status):
        assert client.get(QUEUE_PATH, params={"status": status}).status_code == 200
        return [s.id for s, _, _ in rendered("holiday_review_queue.html")["rows"]]

    assert queue_ids("pending") == [main.id]
    assert queue_ids("reviewed") == []
    first.score = 80
    first.reviewed_at = now
    db.commit()
    assert queue_ids("pending") == []
    assert queue_ids("reviewed") == [main.id]


def test_staff_flow_filters_hide_deleted_links_and_program_structure(
    client, db, user_factory, session_factory,
):
    now = datetime.now(timezone.utc)
    current = _track(db, title="Текущий поток", active=False, sales_until=now - timedelta(days=1))
    future = _track(db, title="Будущий поток", active=False, access_from=now + timedelta(days=1))
    deleted = _track(db, title="Удалённая ссылка", deleted_at=now)
    expired = _track(db, title="Завершённый поток", days=-1)
    deleted_cycle = _track(db, title="Поток с удалённым циклом")
    deleted_stage = _track(db, title="Поток с удалённым этапом")
    deleted_period = _track(db, title="Поток с удалённым периодом")
    detached = _track(db, title="Поток без цикла")
    for track in (current, future, deleted, expired):
        _week(db, track, track.access_from or now - timedelta(days=2))
    cycle = _week(db, deleted_cycle, now)
    cycle.deleted_at = now
    stage_week = _week(db, deleted_stage, now)
    stage = LearningTopic(title="Этап", kind="stage", parent_id=stage_week.parent_id,
                          opens_at=now, deleted_at=now)
    db.add(stage)
    db.flush()
    stage_week.parent_id = stage.id
    period_week = _week(db, deleted_period, now)
    db.get(LearningTopic, period_week.parent_id).deleted_at = now
    student = _newcomer(db, user_factory, profile_completed=True)
    add_tag_to_user(db, student.id, deleted.tag_id)
    add_tag_to_user(db, student.id, deleted_cycle.tag_id)
    db.commit()
    _submit(db, student, cycle, "Сдача из удалённого цикла")
    head = user_factory(vk_id=310_006, role_name="админ")
    curator = user_factory(vk_id=310_007, role_name="куратор")
    _login(client, session_factory, head)

    unavailable = (deleted, expired, deleted_cycle, deleted_stage, deleted_period, detached)
    for path in (QUEUE_PATH, ASSIGN_PATH):
        response = client.get(path)
        assert response.status_code == 200
        assert current.title in response.text
        assert future.title in response.text
        for track in unavailable:
            assert track.title not in response.text
            assert client.get(path, params={"flow": track.id}).status_code == 404
    assert "Сдача из удалённого цикла" not in client.get(QUEUE_PATH).text
    response = client.post(ASSIGN_PATH, data={
        "flow": deleted.id, "curator_id": curator.id, "student_id": [student.id],
    })
    assert response.status_code == 404
    db.refresh(student)
    assert student.curator_id is None


def test_assign_and_review_closed_flow_then_buy_next(
    client, db, user_factory, session_factory, monkeypatch, prodamus, no_delivery,
):
    now = datetime.now(timezone.utc)
    old = _track(db, title="Первый поток")
    next_track = _track(db, title="Следующий поток", days=10, access_from=now + timedelta(days=1))
    first_week = _week(db, old, now - timedelta(days=7))
    second_week = _week(db, next_track, next_track.access_from)
    student = _newcomer(db, user_factory, profile_completed=True)
    student.telegram_chat_id = CHAT_ID
    student.tg_username = "kanikuly"
    student.access_until = now - timedelta(days=1)
    other = _newcomer(db, user_factory, vk_id=310_002, profile_completed=True)
    add_tag_to_user(db, student.id, old.tag_id)
    add_tag_to_user(db, other.id, next_track.tag_id)
    db.commit()
    first = _submit(db, student, first_week, "Разбор первой недели")
    strangers_work = _submit(db, other, second_week, "Чужая сдача")
    curator = user_factory(vk_id=310_003, role_name="куратор")
    second_curator = user_factory(vk_id=310_004, role_name="куратор")
    head = user_factory(vk_id=310_005, role_name="админ")

    _login(client, session_factory, head)
    assert client.get(QUEUE_PATH, params={"flow": old.id, "curator": 0}).status_code == 200
    assert "Разбор первой недели" in client.get(QUEUE_PATH, params={"flow": old.id, "curator": 0}).text
    assert f'name="student_id" value="{student.id}"' in client.get(ASSIGN_PATH, params={"flow": old.id}).text
    assignment = {"flow": old.id, "curator_id": curator.id, "student_id": [student.id]}
    response = client.post(ASSIGN_PATH, data=dict(assignment, student_id=[student.id, other.id]))
    assert response.status_code == 400
    assert student.curator_id is None
    assert client.post(ASSIGN_PATH, data=assignment, follow_redirects=False).status_code == 303
    db.refresh(student)
    assert student.curator_id == curator.id
    assert client.post(ASSIGN_PATH, data=dict(assignment, curator_id=second_curator.id)).status_code == 409
    replacement = dict(assignment, curator_id=second_curator.id, replace_existing="true")
    assert client.post(ASSIGN_PATH, data=replacement, follow_redirects=False).status_code == 303
    db.refresh(student)
    assert student.curator_id == second_curator.id

    work_path = f"/cabinet/staff/task-block-submissions/{first.id}/feedback"
    feedback_path = f"/cabinet/task-block-submissions/{first.id}/feedback"
    _login(client, session_factory, curator)
    assert "Разбор первой недели" not in client.get(QUEUE_PATH).text
    assert client.get(work_path, headers={"Accept": "application/json"}).status_code == 403
    _login(client, session_factory, second_curator)
    queue = client.get(QUEUE_PATH, params={"flow": old.id, "curator": curator.id})
    assert queue.status_code == 200
    assert "Разбор первой недели" in queue.text
    assert "Чужая сдача" not in queue.text
    assert "Разбор первой недели" not in client.get(QUEUE_PATH, params={"flow": next_track.id}).text
    assert client.get(QUEUE_PATH, params={"flow": "", "curator": ""}).status_code == 200
    assert _staff_says(client, session_factory, second_curator, first, "Проверь пропорции", send=False).status_code == 200

    _login(client, session_factory, student)
    assert "Разбор первой недели" not in client.get(HISTORY_PATH).text
    assert client.get(feedback_path).status_code == 404
    _login(client, session_factory, second_curator)
    assert _send(client, first).status_code == 200
    assert "Разбор первой недели" in client.get(QUEUE_PATH, params={"flow": old.id, "status": "reviewed"}).text

    old.access_until = first_week.ends_at = student.access_until
    old.deleted_at = now
    db.commit()
    assert client.get(QUEUE_PATH, params={"flow": old.id, "status": "reviewed"}).status_code == 404
    all_reviewed = client.get(QUEUE_PATH, params={"status": "reviewed"})
    assert "Разбор первой недели" in all_reviewed.text
    assert old.title not in all_reviewed.text

    _login(client, session_factory, student)
    assert "Разбор первой недели" in client.get(HISTORY_PATH).text
    assert "Проверь пропорции" in client.get(feedback_path).text
    assert client.get(f"/cabinet/task-block-submissions/{strangers_work.id}/feedback").status_code == 404
    assert client.post(feedback_path.replace("/feedback", "/messages"), data={"text": "Вопрос"},
                       headers={"Accept": "application/json"}).status_code == 403
    offer = client.get(f"/join/{next_track.slug}", follow_redirects=False)
    assert offer.headers["location"] == f"/cabinet/personal/track?offer={next_track.slug}"

    client.cookies.clear()
    _mock_callback(client, monkeypatch, intake_slug=next_track.slug, member=None)
    assert _callback(client).headers["location"] == offer.headers["location"]
    page = client.get(offer.headers["location"])
    assert f'data-pay-url="/cabinet/personal/track/{next_track.id}/pay"' in page.text
    csrf = client.get("/csrf")
    assert csrf.status_code == 200
    with monkeypatch.context() as scoped:
        scoped.delitem(app.dependency_overrides, require_csrf)
        pay_path = f"/cabinet/personal/track/{next_track.id}/pay"
        assert client.post(pay_path, headers={"Accept": "application/json"}).status_code == 403
        checkout = client.post(pay_path, headers={"X-CSRF-Token": csrf.json()["csrf_token"]})
        assert checkout.status_code == 200
    payment = db.query(Payment).filter_by(user_id=student.id, intake_link_id=next_track.id, kind=KIND_HOLIDAY).one()
    _post_webhook(client, payment)
    db.refresh(payment)
    db.refresh(student)
    assert payment.status == STATUS_PAID
    assert student.curator_id == second_curator.id
    assert not accessible_topic_ids(db, student.id) & {first_week.id, second_week.id}
    assert "Проверь пропорции" in client.get(feedback_path).text

    # Opening the new week advances its schedule without restoring the old one.
    next_track.access_from = now - timedelta(hours=1)
    second_week.opens_at = next_track.access_from
    db.commit()
    assert accessible_topic_ids(db, student.id) & {first_week.id, second_week.id} == {second_week.id}
    history = client.get(feedback_path)
    assert "Проверь пропорции" in history.text
    assert "Твой ответ" not in history.text
    assert client.post(feedback_path.replace("/feedback", "/messages"), data={"text": "Вопрос"},
                       headers={"Accept": "application/json"}).status_code == 403
    _submit(db, student, second_week, "Работа следующей недели")
    client.cookies.clear()
    _login(client, session_factory, second_curator)
    new_queue = client.get(QUEUE_PATH, params={"flow": next_track.id, "status": "all"})
    assert "Работа следующей недели" in new_queue.text
    assert "Разбор первой недели" not in new_queue.text
