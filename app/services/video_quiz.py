"""Мини-опрос из уточняющих вопросов после видео (решение владельца 22.08,
расширено до произвольного числа вопросов 29.08.2026 — см. докстринг
`app/models/video_quiz.py`).

Вопросы задаёт преподаватель в админке видео через `sync_questions`, ответы —
свободный текст, без проверки. Не заводим отдельного экрана для
куратора/преподавателя в этом заходе — ответы только хранятся.
"""

from sqlalchemy.orm import Session as DBSession

from app.models.video_quiz import LearningVideoQuestion, VideoQuizAnswer, VideoQuizResponse


def get_quiz_question_rows(video) -> list[LearningVideoQuestion]:
    """Настроенные строки вопросов по порядку, пустые пропущены.

    `getattr(..., None)`, а не прямой доступ к `video.questions`: у легаси
    пилотного ролика (`SimpleNamespace` из `legacy_pilot_video()`) этой
    связи нет вовсе — у него мини-опроса не бывает.
    """
    return [
        question
        for question in getattr(video, "questions", None) or []
        if question.text and question.text.strip()
    ]


def get_quiz_questions(video) -> list[str]:
    """Тексты настроенных вопросов по порядку — то, что показывается ученику
    и уходит в JSON инлайн-плеера."""
    return [question.text.strip() for question in get_quiz_question_rows(video)]


def get_response(db: DBSession, *, video_id: int, user_id: int) -> VideoQuizResponse | None:
    return (
        db.query(VideoQuizResponse)
        .filter(VideoQuizResponse.video_id == video_id, VideoQuizResponse.user_id == user_id)
        .one_or_none()
    )


def get_answers_map(db: DBSession, *, response_id: int) -> dict[int, str]:
    """question_id → текст ответа, для сборки списка в порядке вопросов на
    экране (см. вызывающий код в app/api/video.py)."""
    return {
        answer.question_id: answer.text or ""
        for answer in db.query(VideoQuizAnswer)
        .filter(VideoQuizAnswer.response_id == response_id)
        .all()
    }


def save_response(
    db: DBSession,
    *,
    video_id: int,
    user_id: int,
    question_rows: list[LearningVideoQuestion],
    answers: list[str],
) -> VideoQuizResponse:
    """Сохранить ответы на настроенные вопросы — по порядку, вопрос к
    вопросу (`question_rows` и `answers` уже проверены вызывающим кодом на
    равную длину).

    Идемпотентно: повторная отправка формы обновляет те же ответы, а не
    заводит вторую строку — уникальность (video_id, user_id) на
    `VideoQuizResponse` и (response_id, question_id) на `VideoQuizAnswer`
    это держат.
    """
    response = get_response(db, video_id=video_id, user_id=user_id)
    if response is None:
        response = VideoQuizResponse(video_id=video_id, user_id=user_id)
        db.add(response)
        db.flush()
    existing = {
        answer.question_id: answer
        for answer in db.query(VideoQuizAnswer)
        .filter(VideoQuizAnswer.response_id == response.id)
        .all()
    }
    for question, value in zip(question_rows, answers):
        text = value.strip() or None
        answer = existing.get(question.id)
        if answer is None:
            db.add(VideoQuizAnswer(response_id=response.id, question_id=question.id, text=text))
        else:
            answer.text = text
    return response


def sync_questions(
    db: DBSession, *, video_id: int, items: list[tuple[int | None, str]]
) -> list[LearningVideoQuestion]:
    """Развести список вопросов из конструктора с уже сохранёнными в базе.

    `items` — пары (id или None, текст) в желаемом порядке из формы, пустые
    ("плюс" без текста) отсекает вызывающий код (JS и валидация payload) до
    сюда не доходят. Правка по id, не полная пересборка: вопрос с тем же id
    сохраняет исходную строку в БД, а с ней и уже сохранённые ответы учеников
    (`video_quiz_answers` ссылается на `question_id`). Вопрос, чей id не
    встретился среди `items` (или новый список короче старого), считается
    удалённым — вместе с ним удаляются и ответы на него: SQLite в тестах не
    исполняет `ON DELETE CASCADE`, поэтому чистим явно, а не полагаемся на
    БД.

    Правит строки таблицы напрямую, не через `video.questions` — если
    вызывающий код читает эту связь в том же запросе после вызова, нужен
    `db.refresh(video)` (так и делают тесты и сам эндпоинт возвращает JSON
    без сериализации вопросов, так что сейчас это не задевает никого, но
    при следующем использовании — задевать может).
    """
    existing = {
        question.id: question
        for question in db.query(LearningVideoQuestion)
        .filter(LearningVideoQuestion.video_id == video_id)
        .all()
    }
    matched_ids: set[int] = set()
    result: list[LearningVideoQuestion] = []
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
            row = LearningVideoQuestion(video_id=video_id, text=text, sort_order=order)
            db.add(row)
        result.append(row)
    for question_id, row in existing.items():
        if question_id in matched_ids:
            continue
        db.query(VideoQuizAnswer).filter(VideoQuizAnswer.question_id == question_id).delete()
        db.delete(row)
    db.flush()
    return result
