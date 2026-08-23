"""Мини-опрос из трёх уточняющих вопросов после видео — сервисный слой."""

from types import SimpleNamespace

from app.models.learning_video import LearningVideo
from app.services.video_quiz import get_quiz_questions, get_response, save_response


def test_get_quiz_questions_skips_blank_slots():
    video = LearningVideo(
        bunny_library_id=1,
        bunny_video_id="v1",
        title="Урок",
        quiz_question_1="Что было самым важным?",
        quiz_question_2="   ",
        quiz_question_3="Как применишь на практике?",
    )
    assert get_quiz_questions(video) == [
        "Что было самым важным?",
        "Как применишь на практике?",
    ]


def test_get_quiz_questions_empty_for_video_without_configured_fields():
    """Легаси пилотный ролик — `SimpleNamespace` без полей опроса вовсе."""
    legacy = SimpleNamespace(id=0, title="Пилотный ролик")
    assert get_quiz_questions(legacy) == []


def test_save_response_is_idempotent_by_video_and_user(db):
    video = LearningVideo(bunny_library_id=1, bunny_video_id="v1", title="Урок")
    db.add(video)
    db.flush()

    save_response(db, video_id=video.id, user_id=1, answers=["Первый ответ"])
    db.commit()
    save_response(db, video_id=video.id, user_id=1, answers=["Обновлённый ответ", "Второй"])
    db.commit()

    response = get_response(db, video_id=video.id, user_id=1)
    assert response.answer_1 == "Обновлённый ответ"
    assert response.answer_2 == "Второй"
    assert response.answer_3 is None


def test_save_response_strips_and_blanks_out_empty_answers(db):
    video = LearningVideo(bunny_library_id=1, bunny_video_id="v1", title="Урок")
    db.add(video)
    db.flush()

    save_response(db, video_id=video.id, user_id=1, answers=["  с пробелами  ", "   "])
    db.commit()

    response = get_response(db, video_id=video.id, user_id=1)
    assert response.answer_1 == "с пробелами"
    assert response.answer_2 is None
