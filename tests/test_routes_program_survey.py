"""Анкета внутри дня учебной программы: конструктор у Главного преподавателя.

Готовый шаблон против новой анкеты — ключевая развилка (owner-решение
22–23.08): анкета показывается несколько раз за год, второе появление не
должно плодить вторую копию вопросов.
"""

from datetime import date

from app.models.survey import QUESTION_SINGLE, QUESTION_TEXT, Survey, SurveyOption, SurveyQuestion
from app.models.tag import Tag
from app.models.tracker import TrackerTask
from app.services.survey import create_survey_with_questions

PROGRAM = "/cabinet/staff/program"
TODAY = date(2026, 8, 21)
MONDAY = "2026-08-24"


def _staff_client(client, user_factory, session_factory, *, vk_id=550_004):
    user = user_factory(
        vk_id=vk_id,
        name="Главный преподаватель",
        is_admin=True,
        is_group_member=False,
        role_name="админ",
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _freeze(monkeypatch, value: date = TODAY):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _audience(tag: Tag) -> dict:
    return {"assign_to_all": False, "tag_ids": [tag.id], "assignee_usernames": ""}


# ── Новая анкета ─────────────────────────────────────────────────────────

def test_new_survey_item_creates_survey_with_questions_and_task(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={
            "title": "Как прошла неделя",
            "questions": [
                {"text": "Что было самым важным?", "question_type": QUESTION_TEXT},
                {
                    "text": "Какой предмет сдал?",
                    "question_type": QUESTION_SINGLE,
                    "options": [
                        {"text": "Рисунок", "is_correct": True},
                        {"text": "Композиция", "is_correct": False},
                    ],
                },
            ],
            "audience": _audience(tag),
        },
    )

    assert response.status_code == 200
    survey = db.query(Survey).one()
    assert survey.title == "Как прошла неделя" and survey.created_by_id == admin.id
    assert db.query(SurveyQuestion).count() == 2
    assert db.query(SurveyOption).count() == 2

    task = db.query(TrackerTask).one()
    assert task.kind == "survey" and task.source_kind == "survey"
    assert task.source_id == survey.id
    assert task.title == "Как прошла неделя"


def test_survey_without_title_is_rejected(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={
            "title": "  ",
            "questions": [{"text": "Вопрос", "question_type": QUESTION_TEXT}],
            "audience": _audience(tag),
        },
    )

    assert response.status_code == 422
    assert db.query(Survey).count() == 0


def test_survey_without_questions_is_rejected(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={"title": "Пустая анкета", "questions": [], "audience": _audience(tag)},
    )

    assert response.status_code == 422
    assert db.query(Survey).count() == 0


# ── Готовый шаблон ───────────────────────────────────────────────────────

def test_existing_survey_is_reused_without_copying_questions(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    survey = create_survey_with_questions(
        db,
        title="Эмоциональное состояние",
        questions=[{"text": "Как самочувствие?", "question_type": QUESTION_TEXT}],
        user_id=admin.id,
    )
    db.commit()

    response = client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={"survey_id": survey.id, "audience": _audience(tag)},
    )

    assert response.status_code == 200
    assert db.query(Survey).count() == 1
    assert db.query(SurveyQuestion).count() == 1
    task = db.query(TrackerTask).one()
    assert task.source_id == survey.id and task.title == "Эмоциональное состояние"


def test_existing_survey_can_be_reused_a_second_time(
    client, db, user_factory, session_factory, monkeypatch
):
    """Восемь точек года — одна и та же анкета выдаётся заново, не блокируется."""
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    survey = create_survey_with_questions(
        db,
        title="Эмоциональное состояние",
        questions=[{"text": "Как самочувствие?", "question_type": QUESTION_TEXT}],
        user_id=admin.id,
    )
    db.commit()

    client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={"survey_id": survey.id, "audience": _audience(tag)},
    )
    second = client.post(
        f"{PROGRAM}/2026-08-31/survey",
        json={"survey_id": survey.id, "audience": _audience(tag)},
    )

    assert second.status_code == 200
    assert db.query(Survey).count() == 1
    assert db.query(TrackerTask).filter(TrackerTask.source_id == survey.id).count() == 2


def test_unknown_survey_id_gives_404(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={"survey_id": 9999, "audience": _audience(tag)},
    )

    assert response.status_code == 404


# ── Общее ────────────────────────────────────────────────────────────────

def test_survey_shows_up_in_the_day(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={
            "title": "Как прошла неделя",
            "questions": [{"text": "Что было важным?", "question_type": QUESTION_TEXT}],
            "audience": _audience(tag),
        },
    )

    page = client.get(f"{PROGRAM}/{MONDAY}").text
    assert "Как прошла неделя" in page
    assert "Вопросов: 1" in page


def test_survey_without_audience_is_rejected(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{PROGRAM}/{MONDAY}/survey",
        json={
            "title": "Анкета",
            "questions": [{"text": "Вопрос", "question_type": QUESTION_TEXT}],
            "audience": {"assign_to_all": False, "tag_ids": [], "assignee_usernames": ""},
        },
    )

    assert response.status_code == 422


def test_past_day_refuses_survey_item(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/2026-08-17/survey",
        json={
            "title": "Задним числом",
            "questions": [{"text": "Вопрос", "question_type": QUESTION_TEXT}],
            "audience": _audience(tag),
        },
    )

    assert response.status_code == 422
