"""Заполнение анкеты учеником — вкладка «Анкета» в АОП.

Зеркало `test_routes_video_quiz.py` и `test_routes_video_tracker_autoclose.py`:
досмотр там решает сервер, здесь тот же принцип — задача закрывается только по
факту реально принятой отправки формы (`submit_response` прошёл без ошибки),
не по клиентской догадке.
"""

from app.models.survey import QUESTION_MULTIPLE, QUESTION_SINGLE, QUESTION_TEXT
from app.models.survey import SurveyAnswer, SurveyResponse
from app.models.tracker import ITEM_HOMEWORK, ITEM_SURVEY, SOURCE_SURVEY, STATUS_DONE, TrackerTaskState
from app.services.survey import create_survey_with_questions, get_questions, options_by_question
from app.services.tracker import create_task

QUESTIONS = [
    {"text": "Как прошла неделя?", "question_type": QUESTION_TEXT},
    {
        "text": "Какой предмет сдавал?",
        "question_type": QUESTION_SINGLE,
        "options": [
            {"text": "Рисунок", "is_correct": False},
            {"text": "Композиция", "is_correct": False},
        ],
    },
    {
        "text": "Что было трудным?",
        "question_type": QUESTION_MULTIPLE,
        "options": [
            {"text": "Пропорции", "is_correct": False},
            {"text": "Свет и тень", "is_correct": False},
        ],
    },
]


def _survey_task(db, user_id: int, *, questions=QUESTIONS, assign_to_all: bool = True):
    survey = create_survey_with_questions(db, title="Анкета недели", questions=questions, user_id=user_id)
    task = create_task(
        db, title=survey.title, user_id=user_id, kind=ITEM_SURVEY,
        source_kind=SOURCE_SURVEY, source_id=survey.id, assign_to_all=assign_to_all,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    db.refresh(survey)
    return task, survey


def test_survey_page_renders_questions_and_options(auth_client, db):
    client, user = auth_client
    task, _ = _survey_task(db, user.id)

    page = client.get(f"/cabinet/survey/{task.id}")

    assert page.status_code == 200
    assert "Как прошла неделя?" in page.text
    assert "Какой предмет сдавал?" in page.text
    assert "Рисунок" in page.text
    assert 'type="radio"' in page.text
    assert 'type="checkbox"' in page.text


def test_survey_page_404_for_non_survey_task(auth_client, db):
    client, user = auth_client
    task = create_task(
        db, title="Домашка", user_id=user.id, kind=ITEM_HOMEWORK, assign_to_all=True,
    )
    task.is_published = True
    db.commit()

    page = client.get(f"/cabinet/survey/{task.id}")

    assert page.status_code == 404


def test_survey_page_404_when_not_addressed_to_student(auth_client, db):
    client, user = auth_client
    task, _ = _survey_task(db, user.id, assign_to_all=False)

    page = client.get(f"/cabinet/survey/{task.id}")

    assert page.status_code == 404


def test_submit_saves_answers_and_closes_task(auth_client, db):
    client, user = auth_client
    task, survey = _survey_task(db, user.id)

    questions = get_questions(db, survey.id)
    text_q = next(q for q in questions if q.question_type == QUESTION_TEXT)
    single_q = next(q for q in questions if q.question_type == QUESTION_SINGLE)
    multiple_q = next(q for q in questions if q.question_type == QUESTION_MULTIPLE)
    options = options_by_question(db, [single_q.id, multiple_q.id])
    single_option = options[single_q.id][0]
    multiple_option_ids = [o.id for o in options[multiple_q.id]]

    response = client.post(
        f"/cabinet/survey/{task.id}/submit",
        json={"answers": [
            {"question_id": text_q.id, "text": "Хорошо прошла"},
            {"question_id": single_q.id, "option_ids": [single_option.id]},
            {"question_id": multiple_q.id, "option_ids": multiple_option_ids},
        ]},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    saved = db.query(SurveyResponse).filter(SurveyResponse.task_id == task.id).one()
    assert saved.user_id == user.id
    assert db.query(SurveyAnswer).filter(SurveyAnswer.response_id == saved.id).count() == 3

    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task.id, TrackerTaskState.user_id == user.id)
        .one()
    )
    assert state.status == STATUS_DONE
    assert state.completion_source == "auto"
    assert state.completed_by_id is None


def test_resubmit_overwrites_previous_answer_not_duplicates(auth_client, db):
    client, user = auth_client
    task, survey = _survey_task(db, user.id, questions=[QUESTIONS[0]])
    question = get_questions(db, survey.id)[0]

    client.post(
        f"/cabinet/survey/{task.id}/submit",
        json={"answers": [{"question_id": question.id, "text": "Первый ответ"}]},
    )
    client.post(
        f"/cabinet/survey/{task.id}/submit",
        json={"answers": [{"question_id": question.id, "text": "Второй ответ"}]},
    )

    assert db.query(SurveyResponse).filter(SurveyResponse.task_id == task.id).count() == 1
    answer = db.query(SurveyAnswer).filter(SurveyAnswer.question_id == question.id).one()
    assert answer.text == "Второй ответ"


def test_submit_unknown_question_is_rejected(auth_client, db):
    client, user = auth_client
    task, survey = _survey_task(db, user.id, questions=[QUESTIONS[0]])

    response = client.post(
        f"/cabinet/survey/{task.id}/submit",
        json={"answers": [{"question_id": 999999, "text": "Мимо анкеты"}]},
    )

    assert response.status_code == 422
    assert db.query(SurveyResponse).filter(SurveyResponse.task_id == task.id).count() == 0


def test_submit_single_choice_with_two_options_is_rejected(auth_client, db):
    client, user = auth_client
    task, survey = _survey_task(db, user.id, questions=[QUESTIONS[1]])
    question = get_questions(db, survey.id)[0]
    option_ids = [o.id for o in options_by_question(db, [question.id])[question.id]]

    response = client.post(
        f"/cabinet/survey/{task.id}/submit",
        json={"answers": [{"question_id": question.id, "option_ids": option_ids}]},
    )

    assert response.status_code == 422


def test_page_prefills_previously_saved_answer(auth_client, db):
    client, user = auth_client
    task, survey = _survey_task(db, user.id, questions=[QUESTIONS[0]])
    question = get_questions(db, survey.id)[0]
    client.post(
        f"/cabinet/survey/{task.id}/submit",
        json={"answers": [{"question_id": question.id, "text": "Сохранённый текст"}]},
    )

    page = client.get(f"/cabinet/survey/{task.id}")

    assert page.status_code == 200
    assert "Сохранённый текст" in page.text
    assert "Ответ уже сохранён" in page.text
