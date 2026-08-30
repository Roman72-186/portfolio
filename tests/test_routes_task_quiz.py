"""Мини-опрос после сдачи элемента дня — общий на material/quiz/lesson/
checklist/homework/survey (владелец 30.08.2026, см. докстринг
`app/models/task_quiz.py`). Видео и Пробник сюда не входят — у них свой
гейт видимости и свои эндпоинты.

Две стороны: конструктор дня (`cabinet_program.py`, staff) заводит и
правит вопросы, эндпоинт `/cabinet/tracker/tasks/{id}/quiz` (`cabinet_tracker.py`,
student) отдаёт их ученику только после того, как задача отмечена сделанной.
"""

from datetime import date, timedelta

from app.models.task_quiz import TaskQuizAnswer, TaskQuizQuestion, TaskQuizResponse
from app.models.tracker import STATUS_DONE, TrackerTask, TrackerTaskState

PROGRAM = "/cabinet/staff/program"

EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}


def _staff_client(client, user_factory, session_factory, *, vk_id=550_100):
    user = user_factory(
        vk_id=vk_id, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _student_client(client, user_factory, session_factory, *, vk_id=550_200):
    student = user_factory(vk_id=vk_id, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)
    return student


def _freeze(monkeypatch, value: date):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)


def _future_day_iso(offset: int = 3) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


# ── Конструктор: создание ────────────────────────────────────────────────────

def test_create_material_with_quiz_questions(client, db, user_factory, session_factory, monkeypatch):
    today = date.today()
    _freeze(monkeypatch, today)
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    resp = client.post(
        f"{PROGRAM}/{day_iso}/material",
        json={
            "title": "Материал",
            "audience": EVERYONE,
            "quiz_questions": [{"text": "Понравилось?"}, {"text": "Что осталось непонятным?"}],
        },
    )
    assert resp.status_code == 200, resp.text

    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()
    rows = (
        db.query(TaskQuizQuestion)
        .filter(TaskQuizQuestion.task_id == task.id)
        .order_by(TaskQuizQuestion.sort_order)
        .all()
    )
    assert [r.text for r in rows] == ["Понравилось?", "Что осталось непонятным?"]


def test_create_without_quiz_questions_leaves_none(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    resp = client.post(
        f"{PROGRAM}/{day_iso}/checklist",
        json={"title": "Чек-лист", "audience": EVERYONE},
    )
    assert resp.status_code == 200
    assert db.query(TaskQuizQuestion).count() == 0


# ── Конструктор: правка сохраняет id, чистит удалённые ──────────────────────

def test_edit_preserves_question_ids_and_removes_missing(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    client.post(
        f"{PROGRAM}/{day_iso}/lesson",
        json={
            "title": "Занятие",
            "audience": EVERYONE,
            "quiz_questions": [{"text": "Вопрос 1"}, {"text": "Вопрос 2"}],
        },
    )
    task = db.query(TrackerTask).filter(TrackerTask.kind == "lesson").one()
    rows = db.query(TaskQuizQuestion).filter(TaskQuizQuestion.task_id == task.id).order_by(TaskQuizQuestion.sort_order).all()
    kept_id = rows[0].id
    dropped_id = rows[1].id

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/lesson",
        json={
            "title": "Занятие",
            "audience": EVERYONE,
            "quiz_questions": [{"id": kept_id, "text": "Вопрос 1 (правка)"}, {"text": "Новый вопрос"}],
        },
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    remaining = db.query(TaskQuizQuestion).filter(TaskQuizQuestion.task_id == task.id).order_by(TaskQuizQuestion.sort_order).all()
    assert [r.id for r in remaining if r.id == kept_id] == [kept_id]
    assert db.get(TaskQuizQuestion, kept_id).text == "Вопрос 1 (правка)"
    assert db.get(TaskQuizQuestion, dropped_id) is None
    assert len(remaining) == 2


def test_edit_with_empty_quiz_questions_clears_them(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    client.post(
        f"{PROGRAM}/{day_iso}/material",
        json={"title": "Материал", "audience": EVERYONE, "quiz_questions": [{"text": "Вопрос"}]},
    )
    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json={"title": "Материал", "audience": EVERYONE, "quiz_questions": []},
    )
    assert resp.status_code == 200
    assert db.query(TaskQuizQuestion).filter(TaskQuizQuestion.task_id == task.id).count() == 0


# ── Ученик: гейт видимости ────────────────────────────────────────────────────

def _material_task_with_quiz(db, staff_user_id) -> TrackerTask:
    from app.services.tracker import create_task
    task = create_task(
        db, title="Материал", user_id=staff_user_id, kind="material", assign_to_all=True,
    )
    task.is_published = True
    db.flush()
    db.add(TaskQuizQuestion(task_id=task.id, text="Как прошло?", sort_order=0))
    db.commit()
    db.refresh(task)
    return task


def test_quiz_endpoint_empty_before_task_done(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_301, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_quiz(db, staff.id)
    _student_client(client, user_factory, session_factory)

    resp = client.get(f"/cabinet/tracker/tasks/{task.id}/quiz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["quiz_questions"] == []
    assert body["quiz_submit_endpoint"] is None


def test_quiz_endpoint_exposes_questions_after_done(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_302, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_quiz(db, staff.id)
    student = _student_client(client, user_factory, session_factory)

    db.add(TrackerTaskState(task_id=task.id, user_id=student.id, status=STATUS_DONE))
    db.commit()

    resp = client.get(f"/cabinet/tracker/tasks/{task.id}/quiz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["quiz_questions"] == ["Как прошло?"]
    assert body["quiz_answers"] is None
    assert body["quiz_submit_endpoint"] == f"/cabinet/tracker/tasks/{task.id}/quiz"


def test_submit_quiz_requires_done_task(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_303, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_quiz(db, staff.id)
    _student_client(client, user_factory, session_factory)

    resp = client.post(f"/cabinet/tracker/tasks/{task.id}/quiz", json={"answers": ["ответ"]})
    assert resp.status_code == 409


# ── Конструктор: разметка дня несёт форму и данные для правки ───────────────

def test_day_page_renders_quiz_block_for_simple_and_homework_forms(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    resp = client.get(f"{PROGRAM}/{day_iso}")
    assert resp.status_code == 200
    # Раскрывашка вопросов есть у каждого из четырёх простых видов и у
    # самостоятельной — ровно 5 карточек на пустой день (видео — своя,
    # шестая, тут не считается: у неё другой текст подсказки).
    assert resp.text.count('class="prg-quiz" data-quiz-questions-wrap>') == 5


def test_edit_payload_includes_quiz_questions_for_prefill(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    client.post(
        f"{PROGRAM}/{day_iso}/quiz",
        json={"title": "Тест", "audience": EVERYONE, "quiz_questions": [{"text": "Вопрос?"}]},
    )
    task = db.query(TrackerTask).filter(TrackerTask.kind == "quiz").one()

    resp = client.get(f"{PROGRAM}/{day_iso}")
    assert resp.status_code == 200
    edit_data_json = resp.text.split("programEditData = ")[1].split(";\n")[0]
    import json as _json
    payload = _json.loads(edit_data_json)[str(task.id)]
    assert payload["quiz_questions"][0]["text"] == "Вопрос?"
    assert isinstance(payload["quiz_questions"][0]["id"], int)


def test_submit_quiz_success_round_trips(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_304, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_quiz(db, staff.id)
    student = _student_client(client, user_factory, session_factory)
    db.add(TrackerTaskState(task_id=task.id, user_id=student.id, status=STATUS_DONE))
    db.commit()

    resp = client.post(f"/cabinet/tracker/tasks/{task.id}/quiz", json={"answers": ["Хорошо"]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert db.query(TaskQuizResponse).filter(
        TaskQuizResponse.task_id == task.id, TaskQuizResponse.user_id == student.id,
    ).count() == 1

    resp = client.get(f"/cabinet/tracker/tasks/{task.id}/quiz")
    assert resp.json()["quiz_answers"] == ["Хорошо"]
