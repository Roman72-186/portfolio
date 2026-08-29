"""Мини-опрос из уточняющих вопросов после видео — сервисный слой."""

from types import SimpleNamespace

from app.models.learning_video import LearningVideo
from app.models.video_quiz import LearningVideoQuestion, VideoQuizAnswer
from app.services.video_quiz import (
    get_answers_map,
    get_quiz_question_rows,
    get_quiz_questions,
    get_response,
    save_response,
    sync_questions,
)


def _video(db) -> LearningVideo:
    video = LearningVideo(bunny_library_id=1, bunny_video_id="v1", title="Урок")
    db.add(video)
    db.flush()
    return video


def test_get_quiz_questions_skips_blank_and_keeps_order(db):
    video = _video(db)
    sync_questions(
        db,
        video_id=video.id,
        items=[(None, "Что было самым важным?"), (None, "Как применишь на практике?")],
    )
    db.commit()
    db.refresh(video)
    assert get_quiz_questions(video) == [
        "Что было самым важным?",
        "Как применишь на практике?",
    ]


def test_get_quiz_questions_empty_for_video_without_configured_fields():
    """Легаси пилотный ролик — `SimpleNamespace` без связи `questions` вовсе."""
    legacy = SimpleNamespace(id=0, title="Пилотный ролик")
    assert get_quiz_questions(legacy) == []


def test_sync_questions_creates_in_given_order(db):
    video = _video(db)
    rows = sync_questions(
        db, video_id=video.id, items=[(None, "Первый"), (None, "Второй")]
    )
    db.commit()
    assert [row.text for row in rows] == ["Первый", "Второй"]
    assert [row.sort_order for row in rows] == [0, 1]


def test_sync_questions_editing_by_id_keeps_answers(db):
    """Правка текста существующего вопроса (тот же id) не должна задевать уже
    сохранённые ответы учеников — только удаление вопроса их удаляет."""
    video = _video(db)
    [question] = sync_questions(db, video_id=video.id, items=[(None, "Черновой текст")])
    db.commit()
    question_id = question.id

    save_response(
        db,
        video_id=video.id,
        user_id=1,
        question_rows=[question],
        answers=["Ответ ученика"],
    )
    db.commit()

    sync_questions(db, video_id=video.id, items=[(question_id, "Финальный текст")])
    db.commit()

    remaining = db.query(LearningVideoQuestion).filter(
        LearningVideoQuestion.video_id == video.id
    ).all()
    assert len(remaining) == 1
    assert remaining[0].id == question_id
    assert remaining[0].text == "Финальный текст"

    response = get_response(db, video_id=video.id, user_id=1)
    assert get_answers_map(db, response_id=response.id) == {question_id: "Ответ ученика"}


def test_sync_questions_removing_id_deletes_question_and_its_answers(db):
    video = _video(db)
    [q1, q2] = sync_questions(
        db, video_id=video.id, items=[(None, "Первый"), (None, "Второй")]
    )
    db.commit()
    save_response(
        db, video_id=video.id, user_id=1, question_rows=[q1, q2], answers=["a1", "a2"]
    )
    db.commit()

    # Только второй вопрос остаётся в новом списке — первый считается удалённым.
    sync_questions(db, video_id=video.id, items=[(q2.id, "Второй")])
    db.commit()

    remaining_ids = {
        row.id
        for row in db.query(LearningVideoQuestion).filter(
            LearningVideoQuestion.video_id == video.id
        )
    }
    assert remaining_ids == {q2.id}
    assert db.query(VideoQuizAnswer).filter(VideoQuizAnswer.question_id == q1.id).count() == 0


def test_sync_questions_with_empty_list_clears_all(db):
    video = _video(db)
    sync_questions(db, video_id=video.id, items=[(None, "Вопрос")])
    db.commit()

    sync_questions(db, video_id=video.id, items=[])
    db.commit()

    assert (
        db.query(LearningVideoQuestion)
        .filter(LearningVideoQuestion.video_id == video.id)
        .count()
        == 0
    )


def test_save_response_is_idempotent_by_video_and_user(db):
    video = _video(db)
    [question] = sync_questions(db, video_id=video.id, items=[(None, "Вопрос")])
    db.commit()
    question_rows = get_quiz_question_rows(video)

    save_response(
        db, video_id=video.id, user_id=1, question_rows=question_rows, answers=["Первый ответ"]
    )
    db.commit()
    save_response(
        db,
        video_id=video.id,
        user_id=1,
        question_rows=question_rows,
        answers=["Обновлённый ответ"],
    )
    db.commit()

    response = get_response(db, video_id=video.id, user_id=1)
    assert get_answers_map(db, response_id=response.id) == {question.id: "Обновлённый ответ"}


def test_save_response_strips_and_blanks_out_empty_answers(db):
    video = _video(db)
    question_rows = sync_questions(db, video_id=video.id, items=[(None, "Вопрос")])
    db.commit()

    save_response(
        db, video_id=video.id, user_id=1, question_rows=question_rows, answers=["  с пробелами  "]
    )
    db.commit()

    response = get_response(db, video_id=video.id, user_id=1)
    assert get_answers_map(db, response_id=response.id) == {
        question_rows[0].id: "с пробелами"
    }
