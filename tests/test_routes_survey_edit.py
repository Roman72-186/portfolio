"""Правка Анкеты (владелец 30.08.2026): доступна, только пока шаблон стоит
ровно в одном дне года (не переиспользуется), и только пока никто ещё не
ответил (`survey.py::has_responses`) — вопросы в этом случае трогать нельзя,
но is_required и мини-опрос остаются доступны.
"""

from datetime import date

from app.models.survey import Survey, SurveyQuestion
from app.models.task_quiz import TaskQuizQuestion
from app.models.tracker import TrackerTask

PROGRAM = "/cabinet/staff/program"
TODAY = date(2026, 8, 21)
MONDAY = "2026-08-24"
TUESDAY = "2026-08-25"
EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}


def _staff_client(client, user_factory, session_factory, *, vk_id=570_001):
    user = user_factory(
        vk_id=vk_id, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _freeze(monkeypatch, value: date = TODAY):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)


def _create_survey(client, day_iso, **extra):
    payload = {
        "title": "Анкета недели",
        "questions": [{"text": "Как настроение?", "question_type": "text", "options": []}],
        "audience": EVERYONE,
    }
    payload.update(extra)
    return client.post(f"{PROGRAM}/{day_iso}/survey", json=payload)


def test_edit_updates_title_and_questions(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_survey(client, MONDAY)
    task = db.query(TrackerTask).filter(TrackerTask.kind == "survey").one()
    survey = db.query(Survey).one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/survey",
        json={
            "title": "Анкета недели (правка)",
            "questions": [
                {"text": "Как настроение сейчас?", "question_type": "text", "options": []},
                {
                    "text": "Что понравилось?", "question_type": "single",
                    "options": [{"text": "Всё", "is_correct": False}, {"text": "Ничего", "is_correct": False}],
                },
            ],
            "is_required": False,
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    survey = db.get(Survey, survey.id)
    assert survey.title == "Анкета недели (правка)"
    questions = db.query(SurveyQuestion).filter(SurveyQuestion.survey_id == survey.id).order_by(SurveyQuestion.sort_order).all()
    assert [q.text for q in questions] == ["Как настроение сейчас?", "Что понравилось?"]
    task = db.get(TrackerTask, task.id)
    assert task.is_required is False
    assert task.title == "Анкета недели (правка)"


def test_edit_updates_quiz_questions(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_survey(client, MONDAY, quiz_questions=[{"text": "Было полезно?"}])
    task = db.query(TrackerTask).filter(TrackerTask.kind == "survey").one()
    old_question = db.query(TaskQuizQuestion).filter(TaskQuizQuestion.task_id == task.id).one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/survey",
        json={
            "title": "Анкета недели",
            "questions": [{"text": "Как настроение?", "question_type": "text", "options": []}],
            "is_required": True,
            "quiz_questions": [{"id": old_question.id, "text": "Было полезно? (правка)"}],
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    rows = db.query(TaskQuizQuestion).filter(TaskQuizQuestion.task_id == task.id).all()
    assert len(rows) == 1
    assert rows[0].id == old_question.id
    assert rows[0].text == "Было полезно? (правка)"


def test_edit_refused_when_survey_used_in_another_day(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_survey(client, MONDAY)
    survey = db.query(Survey).one()
    task = db.query(TrackerTask).filter(TrackerTask.kind == "survey").one()

    client.post(
        f"{PROGRAM}/{TUESDAY}/survey",
        json={"survey_id": survey.id, "audience": EVERYONE},
    )

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/survey",
        json={"title": "Попытка правки", "questions": [], "is_required": True},
    )
    assert resp.status_code == 409
    db.expire_all()
    assert db.get(Survey, survey.id).title == "Анкета недели"


def test_edit_questions_skipped_once_someone_answered(client, db, user_factory, session_factory, monkeypatch):
    """Кто-то уже ответил — вопросы не трогаем (survey.py::has_responses),
    но is_required и заголовок всё равно применяются."""
    from app.services.survey import submit_response

    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_survey(client, MONDAY)
    task = db.query(TrackerTask).filter(TrackerTask.kind == "survey").one()
    survey = db.query(Survey).one()
    question = db.query(SurveyQuestion).filter(SurveyQuestion.survey_id == survey.id).one()

    student = user_factory(vk_id=570_050, name="Ученик", role_name="ученик")
    submit_response(
        db, survey=survey, task_id=task.id, user_id=student.id,
        answers=[{"question_id": question.id, "text": "Отлично", "option_ids": []}],
    )
    db.commit()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/survey",
        json={
            "title": "Новое название",
            "questions": [{"text": "Совсем другой вопрос", "question_type": "text", "options": []}],
            "is_required": False,
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    survey = db.get(Survey, survey.id)
    assert survey.title == "Новое название"
    # Вопрос остался прежним — правка вопросов молча пропущена, не 409.
    questions = db.query(SurveyQuestion).filter(SurveyQuestion.survey_id == survey.id).all()
    assert [q.text for q in questions] == ["Как настроение?"]
    assert db.get(TrackerTask, task.id).is_required is False


def test_edit_refuses_once_day_has_passed(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    _create_survey(client, MONDAY)
    task = db.query(TrackerTask).filter(TrackerTask.kind == "survey").one()

    _freeze(monkeypatch, date(2026, 8, 26))
    resp = client.post(
        f"{PROGRAM}/items/{task.id}/survey",
        json={"title": "Поздно", "questions": [], "is_required": True},
    )
    assert resp.status_code == 422
