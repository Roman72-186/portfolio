"""Мини-опрос после сдачи элемента дня (см. докстринг `app/models/task_quiz.py`).

Работает напрямую по `task_id`, как `mock_exam_quiz.py` работал по
`assignment_id` — без ORM-relationship к `TrackerTask`, вопросы всегда
читаются свежим запросом.
"""

from sqlalchemy.orm import Session as DBSession

from app.models.task_quiz import TaskQuizAnswer, TaskQuizQuestion, TaskQuizResponse


def get_quiz_question_rows(db: DBSession, task_id: int) -> list[TaskQuizQuestion]:
    """Настроенные строки вопросов по порядку."""
    return (
        db.query(TaskQuizQuestion)
        .filter(TaskQuizQuestion.task_id == task_id)
        .order_by(TaskQuizQuestion.sort_order)
        .all()
    )


def get_quiz_questions(db: DBSession, task_id: int) -> list[str]:
    """Тексты настроенных вопросов по порядку — то, что уходит ученику."""
    return [q.text for q in get_quiz_question_rows(db, task_id)]


def get_response(db: DBSession, *, task_id: int, user_id: int) -> TaskQuizResponse | None:
    return (
        db.query(TaskQuizResponse)
        .filter(TaskQuizResponse.task_id == task_id, TaskQuizResponse.user_id == user_id)
        .one_or_none()
    )


def get_answers_map(db: DBSession, *, response_id: int) -> dict[int, str]:
    return {
        answer.question_id: answer.text or ""
        for answer in db.query(TaskQuizAnswer)
        .filter(TaskQuizAnswer.response_id == response_id)
        .all()
    }


def save_response(
    db: DBSession,
    *,
    task_id: int,
    user_id: int,
    question_rows: list[TaskQuizQuestion],
    answers: list[str],
) -> TaskQuizResponse:
    """Сохранить ответы — по порядку, вопрос к вопросу (длины уже сверены
    вызывающим кодом). Идемпотентно: повторная отправка обновляет те же
    ответы, не заводит вторую строку."""
    response = get_response(db, task_id=task_id, user_id=user_id)
    if response is None:
        response = TaskQuizResponse(task_id=task_id, user_id=user_id)
        db.add(response)
        db.flush()
    existing = {
        answer.question_id: answer
        for answer in db.query(TaskQuizAnswer)
        .filter(TaskQuizAnswer.response_id == response.id)
        .all()
    }
    for question, value in zip(question_rows, answers):
        text = value.strip() or None
        answer = existing.get(question.id)
        if answer is None:
            db.add(TaskQuizAnswer(response_id=response.id, question_id=question.id, text=text))
        else:
            answer.text = text
    return response


def create_questions(
    db: DBSession, *, task_id: int, texts: list[str]
) -> list[TaskQuizQuestion]:
    """Завести вопросы для только что созданного элемента — чистое создание.
    Пустые строки уже отсечены вызывающим кодом (JS и `field_validator`
    payload)."""
    rows = []
    for order, text in enumerate(texts):
        row = TaskQuizQuestion(task_id=task_id, text=text, sort_order=order)
        db.add(row)
        rows.append(row)
    return rows


def sync_questions(
    db: DBSession, *, task_id: int, items: list[tuple[int | None, str]]
) -> list[TaskQuizQuestion]:
    """Развести список вопросов из конструктора с уже сохранёнными в базе —
    та же id-сохраняющая логика, что у `video_quiz.py::sync_questions`.

    `items` — пары (id или None, текст) в желаемом порядке из формы, пустые
    ("плюс" без текста) отсекает вызывающий код (JS и валидация payload) до
    сюда не доходят. Правка по id, не полная пересборка: вопрос с тем же id
    сохраняет исходную строку в БД, а с ней и уже сохранённые ответы учеников.
    Вопрос, чей id не встретился среди `items`, считается удалённым — вместе
    с ним удаляются и ответы на него: SQLite в тестах не исполняет
    `ON DELETE CASCADE`, поэтому чистим явно, а не полагаемся на БД.
    """
    existing = {
        question.id: question
        for question in db.query(TaskQuizQuestion)
        .filter(TaskQuizQuestion.task_id == task_id)
        .all()
    }
    matched_ids: set[int] = set()
    result: list[TaskQuizQuestion] = []
    for order, (raw_id, raw_text) in enumerate(items):
        text = (raw_text or "").strip()
        if not text:
            continue
        row = existing.get(raw_id) if raw_id is not None else None
        if row is not None:
            row.text = text
            row.sort_order = order
            matched_ids.add(row.id)
        else:
            row = TaskQuizQuestion(task_id=task_id, text=text, sort_order=order)
            db.add(row)
        result.append(row)
    for question_id, row in existing.items():
        if question_id in matched_ids:
            continue
        db.query(TaskQuizAnswer).filter(TaskQuizAnswer.question_id == question_id).delete()
        db.delete(row)
    db.flush()
    return result
