"""Мини-опрос из трёх уточняющих вопросов после видео (решение владельца 22.08).

Вопросы задаёт преподаватель в админке видео (`LearningVideo.quiz_question_*`),
ответы — свободный текст, без проверки. Не заводим отдельного экрана для
куратора/преподавателя в этом заходе — ответы только хранятся.
"""

from sqlalchemy.orm import Session as DBSession

from app.models.video_quiz import VideoQuizResponse

QUIZ_QUESTION_FIELDS = ("quiz_question_1", "quiz_question_2", "quiz_question_3")
QUIZ_ANSWER_FIELDS = ("answer_1", "answer_2", "answer_3")


def get_quiz_questions(video) -> list[str]:
    """Настроенные вопросы по порядку, пустые пропущены.

    Пропуск, а не позиционное совпадение: преподаватель мог заполнить только
    первый и третий вопрос, и на экране должны остаться два, а не «вопрос 1» +
    пустая карточка. `getattr(..., None)`, а не прямой доступ: у легаси
    пилотного ролика (`SimpleNamespace` из `legacy_pilot_video()`) этих полей
    нет вовсе — у него мини-опроса не бывает.
    """
    return [
        value.strip()
        for field in QUIZ_QUESTION_FIELDS
        if (value := getattr(video, field, None)) and value.strip()
    ]


def get_response(db: DBSession, *, video_id: int, user_id: int) -> VideoQuizResponse | None:
    return (
        db.query(VideoQuizResponse)
        .filter(VideoQuizResponse.video_id == video_id, VideoQuizResponse.user_id == user_id)
        .one_or_none()
    )


def save_response(
    db: DBSession, *, video_id: int, user_id: int, answers: list[str]
) -> VideoQuizResponse:
    """Сохранить ответы на настроенные вопросы (по порядку, до трёх штук).

    Идемпотентно: повторная отправка формы перезаписывает те же три поля, а не
    заводит вторую строку — уникальность (video_id, user_id) это и держит.
    """
    response = get_response(db, video_id=video_id, user_id=user_id)
    if response is None:
        response = VideoQuizResponse(video_id=video_id, user_id=user_id)
        db.add(response)
    for field, value in zip(QUIZ_ANSWER_FIELDS, answers):
        setattr(response, field, value.strip() or None)
    for field in QUIZ_ANSWER_FIELDS[len(answers):]:
        setattr(response, field, None)
    return response
