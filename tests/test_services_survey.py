"""Анкета: конструктор вопросов и сохранение ответов ученика — сервисный слой.

FK на `TrackerTask`/`User` в этих тестах не создаются намеренно (как и у
`test_services_video_quiz.py::test_save_response_is_idempotent_by_video_and_user`,
где `user_id=1` — произвольное число): SQLite в тестах не проверяет внешние
ключи, а сервисный слой анкеты работает только по id, в саму задачу не ходит.
"""

import pytest

from app.models.survey import QUESTION_MULTIPLE, QUESTION_SINGLE, QUESTION_TEXT
from app.services.survey import (
    create_survey_with_questions,
    delete_survey,
    get_answers,
    get_questions,
    get_response,
    has_responses,
    is_attached_to_task,
    options_by_question,
    serialize_for_student,
    set_questions,
    submit_response,
)

QUESTIONS = [
    {
        "text": "Что было самым важным на неделе?",
        "question_type": QUESTION_TEXT,
    },
    {
        "text": "Какой предмет сдал?",
        "question_type": QUESTION_SINGLE,
        "options": [
            {"text": "Рисунок", "is_correct": False},
            {"text": "Композиция", "is_correct": False},
        ],
    },
    {
        "text": "Что вызвало трудности?",
        "question_type": QUESTION_MULTIPLE,
        "options": [
            {"text": "Композиция", "is_correct": True},
            {"text": "Перспектива", "is_correct": True},
            {"text": "Свет", "is_correct": False},
        ],
    },
]


def _make_survey(db, questions=None):
    return create_survey_with_questions(
        db, title="Анкета недели", questions=questions or QUESTIONS, user_id=1
    )


def test_create_survey_with_questions_builds_ordered_options(db):
    survey = _make_survey(db)
    db.commit()

    questions = get_questions(db, survey.id)
    assert [q.text for q in questions] == [q["text"] for q in QUESTIONS]
    assert [q.question_type for q in questions] == [
        QUESTION_TEXT, QUESTION_SINGLE, QUESTION_MULTIPLE,
    ]

    options = options_by_question(db, [q.id for q in questions])
    text_q, single_q, multiple_q = questions
    assert options[text_q.id] == []
    assert [o.text for o in options[single_q.id]] == ["Рисунок", "Композиция"]
    assert [o.is_correct for o in options[multiple_q.id]] == [True, True, False]


def test_set_questions_skips_blank_title_and_options(db):
    survey = _make_survey(db, questions=[
        {"text": "  ", "question_type": QUESTION_TEXT},
        {
            "text": "Вопрос с пустым вариантом",
            "question_type": QUESTION_SINGLE,
            "options": [{"text": "  "}, {"text": "Годится"}],
        },
    ])
    db.commit()

    questions = get_questions(db, survey.id)
    assert len(questions) == 1
    options = options_by_question(db, [questions[0].id])[questions[0].id]
    assert [o.text for o in options] == ["Годится"]


def test_serialize_for_student_hides_is_correct(db):
    survey = _make_survey(db)
    db.commit()

    payload = serialize_for_student(db, survey)
    multiple_question = payload["questions"][2]
    assert multiple_question["options"][0] == {
        "id": multiple_question["options"][0]["id"], "text": "Композиция",
    }
    assert "is_correct" not in multiple_question["options"][0]


def test_set_questions_blocked_once_survey_has_responses(db):
    survey = _make_survey(db)
    db.commit()
    text_question = get_questions(db, survey.id)[0]

    submit_response(
        db, survey=survey, task_id=10, user_id=1,
        answers=[{"question_id": text_question.id, "text": "Всё понравилось"}],
    )
    db.commit()

    assert has_responses(db, survey.id) is True
    with pytest.raises(ValueError, match="survey_has_responses"):
        set_questions(db, survey, [{"text": "Новый вопрос", "question_type": QUESTION_TEXT}])


def test_submit_response_is_idempotent_by_task_and_user(db):
    survey = _make_survey(db)
    db.commit()
    text_q, single_q, _ = get_questions(db, survey.id)
    single_options = options_by_question(db, [single_q.id])[single_q.id]

    submit_response(
        db, survey=survey, task_id=10, user_id=1,
        answers=[
            {"question_id": text_q.id, "text": "Первый ответ"},
            {"question_id": single_q.id, "option_ids": [single_options[0].id]},
        ],
    )
    db.commit()

    submit_response(
        db, survey=survey, task_id=10, user_id=1,
        answers=[{"question_id": text_q.id, "text": "Обновлённый ответ"}],
    )
    db.commit()

    response = get_response(db, task_id=10, user_id=1)
    answers = get_answers(db, response.id)
    assert answers[text_q.id]["text"] == "Обновлённый ответ"
    # Прежний ответ на вопрос про предмет не переносится — резобмит стирает всё.
    assert single_q.id not in answers


def test_submit_response_same_survey_different_task_both_recorded(db):
    """Одна и та же анкета (второй эмоциональный опрос года) снова доступна
    для заполнения в другой неделе — уникальность по task_id, не survey_id."""
    survey = _make_survey(db)
    db.commit()
    text_q = get_questions(db, survey.id)[0]

    submit_response(
        db, survey=survey, task_id=10, user_id=1,
        answers=[{"question_id": text_q.id, "text": "Октябрь"}],
    )
    submit_response(
        db, survey=survey, task_id=20, user_id=1,
        answers=[{"question_id": text_q.id, "text": "Январь"}],
    )
    db.commit()

    first = get_response(db, task_id=10, user_id=1)
    second = get_response(db, task_id=20, user_id=1)
    assert get_answers(db, first.id)[text_q.id]["text"] == "Октябрь"
    assert get_answers(db, second.id)[text_q.id]["text"] == "Январь"


def test_submit_response_rejects_two_options_for_single_choice(db):
    survey = _make_survey(db)
    db.commit()
    single_q = get_questions(db, survey.id)[1]
    options = options_by_question(db, [single_q.id])[single_q.id]

    with pytest.raises(ValueError, match="single_choice_multiple_options"):
        submit_response(
            db, survey=survey, task_id=10, user_id=1,
            answers=[{
                "question_id": single_q.id,
                "option_ids": [o.id for o in options],
            }],
        )


def test_submit_response_rejects_option_from_another_question(db):
    survey = _make_survey(db)
    db.commit()
    single_q, multiple_q = get_questions(db, survey.id)[1], get_questions(db, survey.id)[2]
    foreign_option = options_by_question(db, [multiple_q.id])[multiple_q.id][0]

    with pytest.raises(ValueError, match="unknown_option"):
        submit_response(
            db, survey=survey, task_id=10, user_id=1,
            answers=[{"question_id": single_q.id, "option_ids": [foreign_option.id]}],
        )


def test_delete_survey_blocked_while_attached_to_a_live_task(db, monkeypatch):
    survey = _make_survey(db)
    db.commit()

    monkeypatch.setattr("app.services.survey.is_attached_to_task", lambda db, sid: True)
    with pytest.raises(ValueError, match="survey_attached_to_task"):
        delete_survey(db, survey)


def test_delete_survey_soft_deletes_when_not_attached(db):
    survey = _make_survey(db)
    db.commit()

    assert is_attached_to_task(db, survey.id) is False
    delete_survey(db, survey)
    db.commit()

    assert survey.deleted_at is not None
