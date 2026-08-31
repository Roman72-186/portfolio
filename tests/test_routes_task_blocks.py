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
                {"block_type": BLOCK_PHOTO, "images": [{"url": "https://s3/x.jpg", "path": "p/x.jpg"}]},
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
    assert resp.json()["ok"] is True
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


# ── Ссылка: только http и https (владелец 31.08.2026) ───────────────────────


def test_link_block_rejects_non_http_scheme(client, db, user_factory, session_factory, monkeypatch):
    """Ссылка уходит прямо в href кнопки у ученика. Схема `javascript:` там
    выполнилась бы у каждого, кому видно задание."""
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={
            "title": "Материал",
            "audience": EVERYONE,
            "blocks": [{"block_type": BLOCK_LINK, "url": "javascript:alert(1)"}],
        },
    )
    assert resp.status_code == 422
    assert db.query(TaskBlock).count() == 0


def test_link_block_accepts_http_and_https(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={
            "title": "Материал",
            "audience": EVERYONE,
            "blocks": [
                {"block_type": BLOCK_LINK, "url": "https://example.org", "title": "Раз"},
                {"block_type": BLOCK_LINK, "url": "http://example.org", "title": "Два"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()
    assert [b.url for b in _blocks_of(db, task.id)] == [
        "https://example.org", "http://example.org",
    ]


# ── Вторая очередь: галерея, скрытые вопросы, обязательный верный ответ ─────


def test_choice_question_without_right_answer_is_rejected(client, db, user_factory, session_factory, monkeypatch):
    """Владелец 31.08.2026: лучше не пустить кривой тест в базу, чем потом
    объяснять, почему вопрос не засчитался."""
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={
            "title": "Материал",
            "audience": EVERYONE,
            "blocks": [
                _question("Выбери", question_type=QUESTION_SINGLE,
                          options=[{"text": "А"}, {"text": "Б"}])
            ],
        },
    )
    assert resp.status_code == 422
    assert db.query(TaskBlock).count() == 0


def test_free_text_question_needs_no_right_answer(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)

    resp = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={"title": "Материал", "audience": EVERYONE, "blocks": [_question("Свободный")]},
    )
    assert resp.status_code == 200, resp.text


def test_gallery_and_hidden_flag_round_trip(client, db, user_factory, session_factory, monkeypatch):
    from app.services.task_blocks import get_images

    _freeze(monkeypatch, date.today())
    _staff_client(client, user_factory, session_factory)
    day_iso = _future_day_iso()

    resp = client.post(
        f"{PROGRAM}/{day_iso}/material",
        json={
            "title": "Материал",
            "audience": EVERYONE,
            "blocks": [
                {"block_type": BLOCK_PHOTO, "images": [
                    {"url": "https://s3/1.jpg", "path": "p/1.jpg"},
                    {"url": "https://s3/2.jpg", "path": "p/2.jpg"},
                ]},
                dict(_question("Как прошло?"), hidden_until_done=True),
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()
    photo, question = _blocks_of(db, task.id)
    assert [i.image_s3_url for i in get_images(db, [photo.id])[photo.id]] == [
        "https://s3/1.jpg", "https://s3/2.jpg",
    ]
    assert question.hidden_until_done is True

    # Правка читает всё обратно в форму, ничего не теряя.
    page = client.get(f"{PROGRAM}/{day_iso}")
    payload = _json.loads(page.text.split("programEditData = ")[1].split(";\n")[0])[str(task.id)]
    assert len(payload["blocks"][0]["images"]) == 2
    assert payload["blocks"][1]["hidden_until_done"] is True


# ── Вердикт, одна попытка, скрытые вопросы, гейт ────────────────────────────


def test_verdict_comes_back_with_the_answer_and_on_reload(client, db, user_factory, session_factory):
    """Владелец 31.08.2026: ученик сразу видит результат. И при повторном
    заходе на следующий день — тоже, а не только в момент отправки."""
    staff = user_factory(vk_id=550_401, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(
        db, staff.id,
        blocks=[{"block_type": BLOCK_QUESTION, "question_type": QUESTION_SINGLE, "body": "Сколько?"}],
    )
    [block] = _blocks_of(db, task.id)
    right = TaskBlockOption(block_id=block.id, text="Одна", is_correct=True, sort_order=0)
    db.add(right)
    db.add(TaskBlockOption(block_id=block.id, text="Две", is_correct=False, sort_order=1))
    db.commit()
    _student_client(client, user_factory, session_factory)

    resp = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "option_ids": [right.id]}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["correct_count"] == 1
    assert resp.json()["gradable_count"] == 1

    body = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert body["correct_count"] == 1
    assert body["blocks"][0]["is_correct"] is True


def test_verdict_is_not_leaked_before_answering(client, db, user_factory, session_factory):
    """До ответа сервер не должен подсказывать верный вариант."""
    staff = user_factory(vk_id=550_402, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(
        db, staff.id,
        blocks=[{"block_type": BLOCK_QUESTION, "question_type": QUESTION_SINGLE, "body": "Сколько?"}],
    )
    [block] = _blocks_of(db, task.id)
    db.add(TaskBlockOption(block_id=block.id, text="Одна", is_correct=True, sort_order=0))
    db.commit()
    _student_client(client, user_factory, session_factory)

    body = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert body["answered"] is False
    assert body["correct_count"] is None
    assert body["blocks"][0]["is_correct"] is None
    assert all("is_correct" not in o for o in body["blocks"][0]["options"])


def test_second_attempt_is_rejected(client, db, user_factory, session_factory):
    """Одна попытка: иначе, увидев «неверно», можно было бы переотправить до
    победы, и счёт перестал бы что-либо значить."""
    staff = user_factory(vk_id=550_403, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(db, staff.id)
    [block] = _blocks_of(db, task.id)
    _student_client(client, user_factory, session_factory)

    first = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "text": "Раз"}]},
    )
    assert first.status_code == 200
    second = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "text": "Два"}]},
    )
    assert second.status_code == 409
    # И форма больше не предлагается.
    assert client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["submit_endpoint"] is None


def test_hidden_question_is_invisible_until_task_is_done(client, db, user_factory, session_factory):
    from app.models.tracker import STATUS_DONE, TrackerTaskState

    staff = user_factory(vk_id=550_404, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(db, staff.id, blocks=[
        {"block_type": BLOCK_TEXT, "body": "Материал"},
        {"block_type": BLOCK_QUESTION, "question_type": QUESTION_TEXT,
         "body": "Как прошло?", "hidden_until_done": True},
    ])
    student = _student_client(client, user_factory, session_factory)

    body = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert [b["body"] for b in body["blocks"]] == ["Материал"]

    db.add(TrackerTaskState(task_id=task.id, user_id=student.id, status=STATUS_DONE))
    db.commit()
    body = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert [b["body"] for b in body["blocks"]] == ["Материал", "Как прошло?"]


def test_task_cannot_be_closed_until_visible_questions_answered(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=550_405, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(db, staff.id)
    [block] = _blocks_of(db, task.id)
    _student_client(client, user_factory, session_factory)

    blocked = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert blocked.status_code == 409

    client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "text": "Ответ"}]},
    )
    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 200


def test_hidden_question_does_not_block_closing(client, db, user_factory, session_factory):
    """Развязка тупика: скрытый вопрос не участвует в гейте, иначе задание
    нельзя было бы закрыть никогда — и неделя встала бы за ним."""
    staff = user_factory(vk_id=550_406, name="Стафф", is_admin=True, role_name="админ")
    task = _material_task_with_blocks(db, staff.id, blocks=[
        {"block_type": BLOCK_QUESTION, "question_type": QUESTION_TEXT,
         "body": "Только после сдачи", "hidden_until_done": True},
    ])
    _student_client(client, user_factory, session_factory)

    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 200
