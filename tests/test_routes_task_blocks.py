"""Универсальный конструктор содержимого элемента дня — обе стороны.

Владелец 31.08.2026: в любой вид элемента кладётся что угодно — текст, фото,
видео, ссылка, вопрос — в любом порядке. Заменил прежний мини-опрос
(`test_routes_task_quiz.py`), вопросы стали одним из пяти типов блока.

Staff-сторона — конструктор дня (`cabinet_program.py`), ученическая —
`/cabinet/tracker/tasks/{id}/blocks` (`cabinet_tracker.py`).

Отличие от прежнего мини-опроса, которое тесты и держат: блоки видны ученику
сразу, а не после отметки «сделано». Мини-опрос был рефлексией по факту сдачи,
блоки — содержимое самой задачи.
"""

import json as _json
from datetime import date, timedelta

from app.models.learning_video import LearningVideo
from app.models.task_block import (
    BLOCK_LINK,
    BLOCK_PHOTO,
    BLOCK_QUESTION,
    BLOCK_TEXT,
    BLOCK_VIDEO,
    QUESTION_SINGLE,
    QUESTION_TEXT,
    TaskBlock,
    TaskBlockOption,
    TaskBlockResponse,
)
from app.models.tracker import STATUS_DONE, TrackerTask, TrackerTaskState

PROGRAM = "/cabinet/staff/program"

EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}


def _question(body, *, block_id=None, question_type=QUESTION_TEXT, options=None):
    item = {"block_type": BLOCK_QUESTION, "question_type": question_type, "body": body}
    if block_id is not None:
        item["id"] = block_id
    if options is not None:
        item["options"] = options
    return item


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


def _blocks_of(db, task_id):
    return (
        db.query(TaskBlock)
        .filter(TaskBlock.task_id == task_id)
        .order_by(TaskBlock.sort_order)
        .all()
    )


# ── Конструктор: любой тип содержимого в любом виде элемента ────────────────

def test_material_accepts_every_block_type(client, db, user_factory, session_factory, monkeypatch):
    """Главный контракт стройки: «Материал» больше не только заголовок с
    описанием — в него кладётся видео, фото, ссылка и вопрос."""
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    video = LearningVideo(bunny_library_id=1, bunny_video_id="v-mat", title="Урок")
    db.add(video)
    db.commit()

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={
            "title": "Материал",
            "audience": EVERYONE,
            "blocks": [
                {"block_type": BLOCK_TEXT, "body": "Прочитай перед началом"},
                {"block_type": BLOCK_PHOTO, "image_url": "https://s3/x.jpg", "image_path": "p/x.jpg"},
                {"block_type": BLOCK_VIDEO, "video_id": video.id, "title": "Разбор"},
                {"block_type": BLOCK_LINK, "url": "https://example.org", "title": "Читать"},
                _question("Что было главным?"),
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()
    rows = _blocks_of(db, task.id)
    assert [r.block_type for r in rows] == [
        BLOCK_TEXT, BLOCK_PHOTO, BLOCK_VIDEO, BLOCK_LINK, BLOCK_QUESTION
    ]
    assert rows[2].video_id == video.id
    assert rows[3].url == "https://example.org"


def test_lesson_accepts_blocks_too(client, db, user_factory, session_factory, monkeypatch):
    """Ни один вид элемента не исключение — иначе это не универсальный
    конструктор."""
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/lesson",
        json={
            "title": "Занятие",
            "audience": EVERYONE,
            "blocks": [{"block_type": BLOCK_TEXT, "body": "Что взять с собой"}],
        },
    )
    assert resp.status_code == 200, resp.text
    task = db.query(TrackerTask).filter(TrackerTask.kind == "lesson").one()
    assert [r.body for r in _blocks_of(db, task.id)] == ["Что взять с собой"]


def test_create_without_blocks_leaves_none(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/checklist",
        json={"title": "Чек-лист", "audience": EVERYONE},
    )
    assert resp.status_code == 200
    assert db.query(TaskBlock).count() == 0


def test_question_with_options_round_trips(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/quiz",
        json={
            "title": "Тест",
            "audience": EVERYONE,
            "blocks": [
                _question(
                    "Сколько точек схода у фронтальной перспективы?",
                    question_type=QUESTION_SINGLE,
                    options=[
                        {"text": "Одна", "is_correct": True},
                        {"text": "Две"},
                    ],
                )
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    task = db.query(TrackerTask).filter(TrackerTask.kind == "quiz").one()
    [block] = _blocks_of(db, task.id)
    options = (
        db.query(TaskBlockOption)
        .filter(TaskBlockOption.block_id == block.id)
        .order_by(TaskBlockOption.sort_order)
        .all()
    )
    assert [(o.text, o.is_correct) for o in options] == [("Одна", True), ("Две", False)]


def test_unknown_block_type_is_rejected(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={
            "title": "Материал",
            "audience": EVERYONE,
            "blocks": [{"block_type": "audio", "body": "Подкаст"}],
        },
    )
    assert resp.status_code == 422


# ── Конструктор: правка сохраняет id, чистит удалённые ──────────────────────

def test_edit_preserves_block_ids_and_removes_missing(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    client.post(
        f"{PROGRAM}/{day_iso}/lesson",
        json={
            "title": "Занятие",
            "audience": EVERYONE,
            "blocks": [_question("Вопрос 1"), _question("Вопрос 2")],
        },
    )
    task = db.query(TrackerTask).filter(TrackerTask.kind == "lesson").one()
    rows = _blocks_of(db, task.id)
    kept_id, dropped_id = rows[0].id, rows[1].id

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/lesson",
        json={
            "title": "Занятие",
            "audience": EVERYONE,
            "blocks": [_question("Вопрос 1 (правка)", block_id=kept_id), _question("Новый вопрос")],
        },
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    remaining = _blocks_of(db, task.id)
    assert len(remaining) == 2
    assert db.get(TaskBlock, kept_id).body == "Вопрос 1 (правка)"
    assert db.get(TaskBlock, dropped_id) is None


def test_edit_with_empty_blocks_clears_them(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    client.post(
        f"{PROGRAM}/{day_iso}/material",
        json={"title": "Материал", "audience": EVERYONE, "blocks": [_question("Вопрос")]},
    )
    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json={"title": "Материал", "audience": EVERYONE, "blocks": []},
    )
    assert resp.status_code == 200
    assert db.query(TaskBlock).filter(TaskBlock.task_id == task.id).count() == 0


def test_edit_payload_includes_blocks_for_prefill(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    client.post(
        f"{PROGRAM}/{day_iso}/quiz",
        json={"title": "Тест", "audience": EVERYONE, "blocks": [_question("Вопрос?")]},
    )
    task = db.query(TrackerTask).filter(TrackerTask.kind == "quiz").one()

    resp = client.get(f"{PROGRAM}/{day_iso}")
    assert resp.status_code == 200
    edit_data_json = resp.text.split("programEditData = ")[1].split(";\n")[0]
    payload = _json.loads(edit_data_json)[str(task.id)]
    assert payload["blocks"][0]["body"] == "Вопрос?"
    assert payload["blocks"][0]["block_type"] == BLOCK_QUESTION
    assert isinstance(payload["blocks"][0]["id"], int)


# ── Ученик ───────────────────────────────────────────────────────────────────

def _material_task_with_blocks(db, staff_user_id, *, blocks=None) -> TrackerTask:
    from app.services.tracker import create_task
    task = create_task(
        db, title="Материал", user_id=staff_user_id, kind="material", assign_to_all=True,
    )
    task.is_published = True
    db.flush()
    for order, block in enumerate(blocks or [{"block_type": BLOCK_QUESTION,
                                              "question_type": QUESTION_TEXT,
                                              "body": "Как прошло?"}]):
        db.add(TaskBlock(task_id=task.id, sort_order=order, **block))
    db.commit()
    db.refresh(task)
    return task


def test_blocks_endpoint_shows_content_before_task_done(client, db, user_factory, session_factory):
    """Смена поведения против прежнего мини-опроса: ждать отметки не нужно."""
    staff = user_factory(vk_id=550_301, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(db, staff.id)
    _student_client(client, user_factory, session_factory)

    resp = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks")
    assert resp.status_code == 200
    body = resp.json()
    assert [b["body"] for b in body["blocks"]] == ["Как прошло?"]
    assert body["submit_endpoint"] == f"/cabinet/tracker/tasks/{task.id}/blocks"


def test_blocks_endpoint_without_questions_has_no_submit(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_302, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(
        db, staff.id, blocks=[{"block_type": BLOCK_TEXT, "body": "Просто текст"}]
    )
    _student_client(client, user_factory, session_factory)

    body = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert body["has_questions"] is False
    assert body["submit_endpoint"] is None


def test_blocks_endpoint_hides_correct_answer(client, db, user_factory, session_factory):
    """Ученик не должен видеть, какой вариант верный, в теле ответа сервера."""
    staff = user_factory(vk_id=550_303, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(
        db, staff.id,
        blocks=[{"block_type": BLOCK_QUESTION, "question_type": QUESTION_SINGLE, "body": "Сколько?"}],
    )
    [block] = _blocks_of(db, task.id)
    db.add(TaskBlockOption(block_id=block.id, text="Одна", is_correct=True, sort_order=0))
    db.add(TaskBlockOption(block_id=block.id, text="Две", is_correct=False, sort_order=1))
    db.commit()
    _student_client(client, user_factory, session_factory)

    body = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    options = body["blocks"][0]["options"]
    assert [o["text"] for o in options] == ["Одна", "Две"]
    assert all("is_correct" not in o for o in options)


def test_submit_blocks_round_trips(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_304, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(db, staff.id)
    student = _student_client(client, user_factory, session_factory)
    [block] = _blocks_of(db, task.id)

    resp = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "text": "Хорошо"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert db.query(TaskBlockResponse).filter(
        TaskBlockResponse.task_id == task.id, TaskBlockResponse.user_id == student.id,
    ).count() == 1

    body = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert body["blocks"][0]["answer_text"] == "Хорошо"


def test_submit_blocks_works_after_task_done_too(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_305, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(db, staff.id)
    student = _student_client(client, user_factory, session_factory)
    db.add(TrackerTaskState(task_id=task.id, user_id=student.id, status=STATUS_DONE))
    db.commit()
    [block] = _blocks_of(db, task.id)

    resp = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "text": "Ответ"}]},
    )
    assert resp.status_code == 200


def test_submit_rejects_block_from_another_task(client, db, user_factory, session_factory):
    """`block_id` чужой задачи не должен пройти — сервер клиенту не доверяет."""
    staff = user_factory(vk_id=550_306, name="Стафф", is_admin=True, role_name="админ")
    mine = _material_task_with_blocks(db, staff.id)
    other = _material_task_with_blocks(db, staff.id)
    _student_client(client, user_factory, session_factory)
    [foreign] = _blocks_of(db, other.id)

    resp = client.post(
        f"/cabinet/tracker/tasks/{mine.id}/blocks",
        json={"answers": [{"block_id": foreign.id, "text": "Ответ"}]},
    )
    assert resp.status_code == 422


def test_submit_on_task_without_questions_is_404(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_307, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(
        db, staff.id, blocks=[{"block_type": BLOCK_TEXT, "body": "Текст"}]
    )
    _student_client(client, user_factory, session_factory)

    resp = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": 1, "text": "Ответ"}]},
    )
    assert resp.status_code == 404
