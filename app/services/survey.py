"""Анкета: конструктор вопросов преподавателя, сохранение ответов ученика.

Черновой конструктор (владелец, TODO.md §13) — эта стройка закрывает только
модель и сервисный слой. Экран сборки анкеты у преподавателя (конструктор на
дне программы) и экран заполнения у ученика (вкладка «Анкета» в АОП) — не
здесь, отдельным заходом. Разбор/сегментация ответов, в т.ч. «красная зона» по
эмоциональному состоянию, — тоже отдельная стройка позже, эта модель только
хранит ответы как есть.

Без ORM `relationship()` — как и у остальных моделей проекта (`homework.py`,
`tracker.py`): связи собираются явными запросами, не декларативным маппингом.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.survey import (
    QUESTION_SINGLE,
    QUESTION_TEXT,
    QUESTION_TYPES,
    Survey,
    SurveyAnswer,
    SurveyAnswerOption,
    SurveyOption,
    SurveyQuestion,
    SurveyResponse,
)
from app.models.tracker import SOURCE_SURVEY, TrackerTask


def get_survey(db: Session, survey_id: int) -> Survey | None:
    survey = db.get(Survey, survey_id)
    if survey is None or survey.deleted_at is not None:
        return None
    return survey


def get_questions(db: Session, survey_id: int) -> list[SurveyQuestion]:
    return (
        db.query(SurveyQuestion)
        .filter(SurveyQuestion.survey_id == survey_id)
        .order_by(SurveyQuestion.sort_order.asc(), SurveyQuestion.id.asc())
        .all()
    )


def options_by_question(db: Session, question_ids: list[int]) -> dict[int, list[SurveyOption]]:
    """Варианты ответа, сгруппированные по вопросу — один запрос на всю анкету."""
    if not question_ids:
        return {}
    rows = (
        db.query(SurveyOption)
        .filter(SurveyOption.question_id.in_(question_ids))
        .order_by(SurveyOption.sort_order.asc(), SurveyOption.id.asc())
        .all()
    )
    grouped: dict[int, list[SurveyOption]] = {qid: [] for qid in question_ids}
    for option in rows:
        grouped.setdefault(option.question_id, []).append(option)
    return grouped


def has_responses(db: Session, survey_id: int) -> bool:
    """Хоть один ученик уже ответил — конструктор вопросов больше не
    перезаписывать: правка задним числом осиротила бы уже сохранённые ответы
    (`SurveyAnswer.question_id` каскадно исчез бы вместе с вопросом)."""
    return (
        db.query(SurveyResponse.id).filter(SurveyResponse.survey_id == survey_id).first()
        is not None
    )


def is_attached_to_task(db: Session, survey_id: int) -> bool:
    """Анкета стоит хотя бы в одной живой неделе — как у тем видео,
    удалять контент, на который всё ещё ссылается расписание, нельзя."""
    return (
        db.query(TrackerTask.id)
        .filter(
            TrackerTask.source_kind == SOURCE_SURVEY,
            TrackerTask.source_id == survey_id,
            TrackerTask.deleted_at.is_(None),
        )
        .first()
        is not None
    )


def create_survey(db: Session, *, title: str, user_id: int) -> Survey:
    survey = Survey(title=title, created_by_id=user_id)
    db.add(survey)
    db.flush()
    return survey


def update_survey_title(survey: Survey, *, title: str) -> None:
    survey.title = title


def set_questions(db: Session, survey: Survey, questions: list[dict]) -> None:
    """Переписать вопросы и варианты целиком.

    `questions` — список `{"text": str, "question_type": str, "options": [...]}`,
    `options` — список `{"text": str, "is_correct": bool}`, актуален только для
    типов single/multiple. Запрещено, если анкета уже кем-то заполнена
    (см. `has_responses`) — вызывающий код обязан проверить это раньше и вернуть
    понятную ошибку, здесь только последний рубеж.
    """
    if has_responses(db, survey.id):
        raise ValueError("survey_has_responses")

    existing_ids = [q.id for q in get_questions(db, survey.id)]
    if existing_ids:
        db.query(SurveyOption).filter(SurveyOption.question_id.in_(existing_ids)).delete(
            synchronize_session=False
        )
        db.query(SurveyQuestion).filter(SurveyQuestion.id.in_(existing_ids)).delete(
            synchronize_session=False
        )

    for q_order, question in enumerate(questions):
        text = (question.get("text") or "").strip()
        if not text:
            continue
        question_type = question.get("question_type")
        if question_type not in QUESTION_TYPES:
            question_type = QUESTION_TEXT
        row = SurveyQuestion(
            survey_id=survey.id,
            text=text,
            question_type=question_type,
            sort_order=q_order,
        )
        db.add(row)
        db.flush()

        if question_type == QUESTION_TEXT:
            continue
        for o_order, option in enumerate(question.get("options") or []):
            option_text = (option.get("text") or "").strip()
            if not option_text:
                continue
            db.add(
                SurveyOption(
                    question_id=row.id,
                    text=option_text,
                    is_correct=bool(option.get("is_correct")),
                    sort_order=o_order,
                )
            )
    db.flush()


def create_survey_with_questions(
    db: Session, *, title: str, questions: list[dict], user_id: int
) -> Survey:
    survey = create_survey(db, title=title, user_id=user_id)
    set_questions(db, survey, questions)
    return survey


def delete_survey(db: Session, survey: Survey) -> None:
    """Мягкое удаление. Отказ, если анкета ещё стоит в живой неделе — снятие
    контента, на который ссылается расписание, тихо обнулило бы вкладку
    «Анкета» у всех адресатов (тот же fail-closed риск, что у тем видео)."""
    if is_attached_to_task(db, survey.id):
        raise ValueError("survey_attached_to_task")
    survey.deleted_at = datetime.now(timezone.utc)


def serialize_for_student(db: Session, survey: Survey) -> dict:
    """Вопросы и варианты без `is_correct` — ученику правильный ответ не
    показываем, разбор ответов не в этой стройке."""
    questions = get_questions(db, survey.id)
    options = options_by_question(db, [q.id for q in questions])
    return {
        "id": survey.id,
        "title": survey.title,
        "questions": [
            {
                "id": question.id,
                "text": question.text,
                "question_type": question.question_type,
                "options": [
                    {"id": option.id, "text": option.text}
                    for option in options.get(question.id, [])
                ],
            }
            for question in questions
        ],
    }


def get_response(db: Session, *, task_id: int, user_id: int) -> SurveyResponse | None:
    return (
        db.query(SurveyResponse)
        .filter(SurveyResponse.task_id == task_id, SurveyResponse.user_id == user_id)
        .one_or_none()
    )


def get_answers(db: Session, response_id: int) -> dict[int, dict]:
    """Ответы одной анкеты по question_id: `{"text": str|None, "option_ids": [...]}`."""
    answers = (
        db.query(SurveyAnswer).filter(SurveyAnswer.response_id == response_id).all()
    )
    if not answers:
        return {}
    answer_ids = [a.id for a in answers]
    option_rows = (
        db.query(SurveyAnswerOption)
        .filter(SurveyAnswerOption.answer_id.in_(answer_ids))
        .all()
    )
    options_by_answer: dict[int, list[int]] = {}
    for row in option_rows:
        options_by_answer.setdefault(row.answer_id, []).append(row.option_id)
    return {
        answer.question_id: {
            "text": answer.text,
            "option_ids": options_by_answer.get(answer.id, []),
        }
        for answer in answers
    }


def submit_response(
    db: Session, *, survey: Survey, task_id: int, user_id: int, answers: list[dict]
) -> SurveyResponse:
    """Сохранить ответы ученика. Идемпотентно: повторная отправка формы того
    же появления анкеты (того же `task_id`) перезаписывает прошлый ответ, как
    у мини-опроса после видео (`video_quiz.py`), а не заводит вторую строку.

    `answers` — список `{"question_id": int, "text": str|None,
    "option_ids": list[int]}`. Вопрос, которого нет в списке, остаётся без
    ответа — заполнение анкеты целиком не форсируется здесь.
    """
    questions = {q.id: q for q in get_questions(db, survey.id)}
    valid_option_ids: dict[int, set[int]] = {
        qid: {o.id for o in opts}
        for qid, opts in options_by_question(db, list(questions.keys())).items()
    }

    response = get_response(db, task_id=task_id, user_id=user_id)
    if response is None:
        response = SurveyResponse(survey_id=survey.id, task_id=task_id, user_id=user_id)
        db.add(response)
        db.flush()
    else:
        db.query(SurveyAnswer).filter(SurveyAnswer.response_id == response.id).delete(
            synchronize_session=False
        )
        db.flush()

    for answer in answers:
        question_id = answer.get("question_id")
        question = questions.get(question_id)
        if question is None:
            raise ValueError("unknown_question")

        text = answer.get("text")
        option_ids = [oid for oid in (answer.get("option_ids") or [])]

        if question.question_type == QUESTION_TEXT:
            text = (text or "").strip() or None
            option_ids = []
        else:
            allowed = valid_option_ids.get(question_id, set())
            if any(oid not in allowed for oid in option_ids):
                raise ValueError("unknown_option")
            if question.question_type == QUESTION_SINGLE and len(option_ids) > 1:
                raise ValueError("single_choice_multiple_options")
            text = None

        if text is None and not option_ids:
            continue

        row = SurveyAnswer(response_id=response.id, question_id=question_id, text=text)
        db.add(row)
        db.flush()
        for option_id in option_ids:
            db.add(SurveyAnswerOption(answer_id=row.id, option_id=option_id))

    db.flush()
    return response
