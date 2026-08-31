"""Правка Пробника (владелец 30.08.2026): билеты, обязательность, мини-опрос.

Править билет можно всегда, даже если ученик уже сдаёт по нему — снимок
текста уже лежит в `MockExamAttempt.ticket_title`/`.ticket_description`/
`.ticket_image_url` (владелец подтвердил явно). Убрать билет, по которому
уже есть цикл сдачи (`ExamCycle`), нельзя — внешний ключ без каскада
отклоняет удаление, эндпоинт ловит это как 409.
"""

import json
from datetime import date, datetime, timedelta, timezone

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.learning_topic import LearningTopic
from app.models.task_quiz import TaskQuizQuestion
from app.models.tracker import TrackerTask
from app.services.exam_tickets import get_ticket_tariffs
from app.services.video_topics import get_topic_tariffs

PROGRAM = "/cabinet/staff/program"
EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}


def _staff_client(client, user_factory, session_factory, *, vk_id=560_001):
    user = user_factory(
        vk_id=vk_id, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _freeze(monkeypatch, value: date | None = None):
    # Настоящее "сегодня" плюс день на неделю вперёд (см. test_routes_program_mock.py,
    # комментарий у _future_day_iso) — иначе схема билета (validate_window) с
    # захардкоженной прошлой датой считает окно билета уже истёкшим.
    value = value or date.today()
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.exam_tickets.today_msk", lambda: value)


def _future_day_iso(offset: int = 7) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _ticket(title="Натюрморт"):
    return {"title": title, "description": "Два листа"}


def _create_mock(client, day_iso, *, subject="Рисунок", tickets=None, quiz_questions=None):
    payload = {
        "subjects": [{"subject": subject, "tickets": tickets or [_ticket()]}],
        "audience": EVERYONE,
    }
    if quiz_questions is not None:
        payload["quiz_questions"] = quiz_questions
    return client.post(f"{PROGRAM}/{day_iso}/mock", json=payload)


def test_edit_ticket_title_in_place_preserves_id(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    create_resp = _create_mock(client, _future_day_iso())
    assert create_resp.status_code == 200, create_resp.text
    ticket = db.query(ExamTicket).one()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [{"id": ticket.id, "title": "Правленый натюрморт", "description": "Ваза"}],
            "is_required": True,
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    refreshed = db.get(ExamTicket, ticket.id)
    assert refreshed.title == "Правленый натюрморт"
    assert refreshed.description == "Ваза"
    assert db.query(ExamTicket).count() == 1


def test_mock_tariff_restriction_round_trips_on_create_and_edit(
    client, db, user_factory, session_factory, monkeypatch
):
    """Пробник — единственный вид, где адресация не редактируется, но тариф
    (владелец 26.08.2026) — исключение, как is_required."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    create_resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/mock",
        json={
            "subjects": [{"subject": "Рисунок", "tickets": [_ticket()]}],
            "audience": {
                "assign_to_all": True, "tag_ids": [], "assignee_usernames": "",
                "tariff_restricted": True, "tariffs": ["МАКСИМУМ"],
            },
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    ticket = db.query(ExamTicket).one()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()
    assert ticket.tariff_restricted is True
    assert get_ticket_tariffs(db, ticket.id) == ["МАКСИМУМ"]
    topic = db.get(LearningTopic, task.topic_id)
    assert topic.tariff_restricted is True
    assert get_topic_tariffs(db, topic.id) == ["МАКСИМУМ"]

    edit_resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [{"id": ticket.id, "title": ticket.title, "description": ticket.description}],
            "is_required": True,
            "tariff_restricted": False,
            "tariffs": [],
        },
    )
    assert edit_resp.status_code == 200, edit_resp.text
    db.expire_all()
    ticket = db.get(ExamTicket, ticket.id)
    assert ticket.tariff_restricted is False
    assert get_ticket_tariffs(db, ticket.id) == []
    topic = db.get(LearningTopic, task.topic_id)
    assert topic.tariff_restricted is False
    assert get_topic_tariffs(db, topic.id) == []


def test_edit_adds_new_ticket(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_mock(client, _future_day_iso())
    ticket = db.query(ExamTicket).one()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [
                {"id": ticket.id, "title": ticket.title, "description": ticket.description},
                {"title": "Второй билет", "description": "Гипсовая голова"},
            ],
            "is_required": True,
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    tickets = db.query(ExamTicket).order_by(ExamTicket.ticket_number).all()
    assert len(tickets) == 2
    assert tickets[0].id == ticket.id
    assert tickets[1].title == "Второй билет"
    assert tickets[1].ticket_number == 2


def test_edit_removes_ticket_without_cycle(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_mock(client, _future_day_iso(), tickets=[_ticket("Билет 1"), _ticket("Билет 2")])
    tickets = db.query(ExamTicket).order_by(ExamTicket.ticket_number).all()
    keep = tickets[0]
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [{"id": keep.id, "title": keep.title, "description": keep.description}],
            "is_required": True,
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert db.query(ExamTicket).count() == 1
    assert db.query(ExamTicket).one().id == keep.id


def test_edit_removing_ticket_with_open_cycle_returns_409(client, db, user_factory, session_factory, monkeypatch):
    """Убрать билет, по которому уже есть сдача, нельзя — FK без каскада
    ExamCycle.ticket_id отклоняет удаление, эндпоинт отдаёт понятный 409,
    а не 500."""
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)
    _create_mock(client, _future_day_iso(), tickets=[_ticket("Билет 1"), _ticket("Билет 2")])
    tickets = db.query(ExamTicket).order_by(ExamTicket.ticket_number).all()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()

    student = user_factory(vk_id=560_050, name="Ученик", role_name="ученик")
    cycle = ExamCycle(
        user_id=student.id, subject="Рисунок", ticket_id=tickets[1].id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(cycle)
    db.commit()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [{"id": tickets[0].id, "title": tickets[0].title, "description": tickets[0].description}],
            "is_required": True,
        },
    )
    assert resp.status_code == 409
    db.expire_all()
    # Транзакция откатилась — оба билета на месте, ничего не потеряно.
    assert db.query(ExamTicket).count() == 2


def test_edit_title_stays_safe_while_attempt_in_progress(client, db, user_factory, session_factory, monkeypatch):
    """Владелец 30.08.2026: править билет можно всегда — снимок текста уже
    лежит в MockExamAttempt на момент старта попытки, правка его не трогает."""
    from app.models.mock_exam_attempt import MockExamAttempt

    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_mock(client, _future_day_iso())
    ticket = db.query(ExamTicket).one()
    original_title = ticket.title
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()

    student = user_factory(vk_id=560_060, name="Ученик", role_name="ученик")
    attempt = MockExamAttempt(
        user_id=student.id, subject="Рисунок", ticket_id=ticket.id,
        ticket_title=ticket.title, ticket_description=ticket.description,
        started_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.commit()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [{"id": ticket.id, "title": "Новый текст билета", "description": "Новое описание"}],
            "is_required": True,
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    # Билет в базе изменился...
    assert db.get(ExamTicket, ticket.id).title == "Новый текст билета"
    # ...но снимок в уже начатой попытке — нет, ученик доделывает старый билет.
    assert db.get(MockExamAttempt, attempt.id).ticket_title == original_title


def test_edit_is_required_and_quiz_questions(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_mock(client, _future_day_iso(), quiz_questions=[{"text": "Как прошло?"}])
    ticket = db.query(ExamTicket).one()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()
    old_question = db.query(TaskQuizQuestion).filter(TaskQuizQuestion.task_id == task.id).one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [{"id": ticket.id, "title": ticket.title, "description": ticket.description}],
            "is_required": False,
            "quiz_questions": [{"id": old_question.id, "text": "Как прошло? (правка)"}, {"text": "Ещё вопрос"}],
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    task = db.get(TrackerTask, task.id)
    assert task.is_required is False
    rows = db.query(TaskQuizQuestion).filter(TaskQuizQuestion.task_id == task.id).order_by(TaskQuizQuestion.sort_order).all()
    assert [r.text for r in rows] == ["Как прошло? (правка)", "Ещё вопрос"]
    assert rows[0].id == old_question.id


def test_edit_refuses_once_day_has_passed(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_mock(client, _future_day_iso())
    ticket = db.query(ExamTicket).one()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()

    _freeze(monkeypatch, date.today() + timedelta(days=8))
    resp = client.post(
        f"{PROGRAM}/items/{task.id}/mock",
        json={
            "tickets": [{"id": ticket.id, "title": "Поздно", "description": ""}],
            "is_required": True,
        },
    )
    assert resp.status_code == 422


def test_day_page_offers_edit_for_mock_and_prefills_tickets(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_mock(client, _future_day_iso())
    ticket = db.query(ExamTicket).one()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "mock_exam").one()

    page = client.get(f"{PROGRAM}/{_future_day_iso()}").text
    article = page[page.index(f'data-item-id="{task.id}"'):]
    assert 'data-item-edit' in article[:article.index('</article>')]

    edit_data_json = page.split('programEditData = ')[1].split(';\n')[0]
    edit_data = json.loads(edit_data_json)
    payload = edit_data[str(task.id)]
    assert payload["tickets"][0]["id"] == ticket.id
    assert payload["tickets"][0]["title"] == ticket.title
