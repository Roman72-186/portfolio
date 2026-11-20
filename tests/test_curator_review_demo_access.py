"""Новая проверка только для ГП, старая форма доступна кураторам."""
import pytest

from app.models.task_block_feedback import TaskBlockFeedbackMessage
from tests.test_feedback_rating import _setup_block, _login, _send, _quiet_side_effects


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
    curator, _, _, submission = _setup_block(db, user_factory, base=998_301)
    chief = user_factory(vk_id=998_311, role_name="админ")
    _login(client, session_factory, chief)
    queue = client.get("/cabinet/staff/students-review/queue", params={"review_curator": curator.id})
    assert queue.status_code == 200
    assert "Проверка работ · демо" in queue.text
    base = f"/cabinet/staff/task-block-submissions/{submission.id}"
    page = client.get(base + "/feedback?review_demo=1")
    assert page.status_code == 200
    assert 'id="review-section"' in page.text
    assert "data-review-draft-url=" in page.text
    assert client.put(base + "/draft", json={"text": "Демо ГП", "cursor": 0}).status_code == 200


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
