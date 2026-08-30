"""Мини-опрос после сдачи Пробника (см. докстринг `app/models/mock_exam_quiz.py`).

В отличие от `app/services/video_quiz.py` работает по `assignment_id`
напрямую, не по ORM-объекту с `.questions` — у `ExamAssignment` нет и не
заводился обратный relationship ради этого, вопросы всегда читаются свежим
запросом.
"""

from sqlalchemy.orm import Session as DBSession

from app.models.mock_exam_quiz import ExamAssignmentQuestion, MockQuizAnswer, MockQuizResponse


def get_quiz_question_rows(db: DBSession, assignment_id: int) -> list[ExamAssignmentQuestion]:
    """Настроенные строки вопросов по порядку."""
    return (
        db.query(ExamAssignmentQuestion)
        .filter(ExamAssignmentQuestion.assignment_id == assignment_id)
        .order_by(ExamAssignmentQuestion.sort_order)
        .all()
    )


def get_quiz_questions(db: DBSession, assignment_id: int) -> list[str]:
    """Тексты настроенных вопросов по порядку — то, что уходит ученику."""
    return [q.text for q in get_quiz_question_rows(db, assignment_id)]


def get_response(db: DBSession, *, assignment_id: int, user_id: int) -> MockQuizResponse | None:
    return (
        db.query(MockQuizResponse)
        .filter(
            MockQuizResponse.assignment_id == assignment_id,
            MockQuizResponse.user_id == user_id,
        )
        .one_or_none()
    )


def get_answers_map(db: DBSession, *, response_id: int) -> dict[int, str]:
    return {
        answer.question_id: answer.text or ""
        for answer in db.query(MockQuizAnswer)
        .filter(MockQuizAnswer.response_id == response_id)
        .all()
    }


def save_response(
    db: DBSession,
    *,
    assignment_id: int,
    user_id: int,
    question_rows: list[ExamAssignmentQuestion],
    answers: list[str],
) -> MockQuizResponse:
    """Сохранить ответы — по порядку, вопрос к вопросу (длины уже сверены
    вызывающим кодом). Идемпотентно: повторная отправка обновляет те же
    ответы, не заводит вторую строку."""
    response = get_response(db, assignment_id=assignment_id, user_id=user_id)
    if response is None:
        response = MockQuizResponse(assignment_id=assignment_id, user_id=user_id)
        db.add(response)
        db.flush()
    existing = {
        answer.question_id: answer
        for answer in db.query(MockQuizAnswer)
        .filter(MockQuizAnswer.response_id == response.id)
        .all()
    }
    for question, value in zip(question_rows, answers):
        text = value.strip() or None
        answer = existing.get(question.id)
        if answer is None:
            db.add(MockQuizAnswer(response_id=response.id, question_id=question.id, text=text))
        else:
            answer.text = text
    return response


def create_questions(
    db: DBSession, *, assignment_id: int, texts: list[str]
) -> list[ExamAssignmentQuestion]:
    """Завести вопросы для только что созданного задания — чистое создание,
    не правка: у Пробника нет экрана редактирования (см. `cabinet_program.py`,
    `editable_kinds`), поэтому не нужна id-сохраняющая развязка, как у
    `video_quiz.py::sync_questions`. Пустые строки уже отсечены вызывающим
    кодом (JS и `field_validator` payload)."""
    rows = []
    for order, text in enumerate(texts):
        row = ExamAssignmentQuestion(assignment_id=assignment_id, text=text, sort_order=order)
        db.add(row)
        rows.append(row)
    return rows
