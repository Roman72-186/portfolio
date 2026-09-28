"""Блок «Сравнение работ» (Лиза, голосовое 27.09.2026).

Ученик видит работы парами и выбирает ту, что «наберёт больше баллов»;
выбранная остаётся, к ней приходит следующая. В финале — совпал ли выбор с
выбором преподавателя. Перебор пар живёт в браузере, на сервер уходит только
победившая работа.

Что здесь держится:
- выбор преподавателя (`is_pick`) не уходит ученику до его ответа;
- ответ хранится URL-ом картинки и переживает пересохранение блока, хотя
  `_sync_images` пересоздаёт картинки с новыми id;
- одна попытка, срок запирает отправку, чужой адрес не принимается;
- ответ закрывает блок и открывает ленту ниже;
- преподаватель видит «Выбрал: Работа №k / Верно: Работа №m» на экране проверки.
"""
from datetime import date, datetime, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_COMPARE,
    BLOCK_PHOTO,
    BLOCK_TEXT,
    TaskBlock,
    TaskBlockAnswer,
    TaskBlockImage,
)
from app.models.tracker import TrackerTask
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.task_blocks import (
    get_images,
    get_state,
    review_queue,
    sync_blocks,
)
from app.services.tracker import copy_task_blocks, create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)

PROGRAM = "/cabinet/staff/program"
EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}

WORKS = [f"https://s3/works/{n}.jpg" for n in range(1, 6)]
PICK = WORKS[2]


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    db.add(LearningTopic(
        title="Годовой курс", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    ))
    db.commit()


def _task(db, owner, *, title="Сравнение"):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY + timedelta(days=2))[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _images(pick=PICK, works=WORKS):
    return [
        {"url": url, "path": url.rsplit("/", 1)[-1], "is_pick": url == pick}
        for url in works
    ]


def _compare_item(*, block_id=None, pick=PICK, works=WORKS, required=True):
    item = {
        "block_type": BLOCK_COMPARE,
        "title": "Выбери работу, которая наберёт больше баллов",
        "is_required": required,
        "images": _images(pick, works),
    }
    if block_id is not None:
        item["id"] = block_id
    return item


def _compare_block(db, task, *, tail=True, **kwargs):
    items = [_compare_item(**kwargs)]
    if tail:
        items.append({"block_type": BLOCK_TEXT, "body": "Разбор работ"})
    blocks = sync_blocks(db, task_id=task.id, items=items)
    db.commit()
    return blocks[0]


def _statuses(db, user):
    steps = build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )
    return [step["status"] for step in steps]


def _choose(client, block_id, url):
    return client.post(
        f"/cabinet/tracker/blocks/{block_id}/compare", json={"image_url": url}
    )


def _feed_item(client, task_id, block_id):
    payload = client.get(f"/cabinet/tracker/tasks/{task_id}/blocks").json()
    return next(b for b in payload["blocks"] if b["id"] == block_id)


# ── конструктор ─────────────────────────────────────────────────────────────

def test_block_keeps_works_in_order_with_one_pick(db, regular_user):
    task = _task(db, regular_user)
    block = _compare_block(db, task, tail=False)

    images = get_images(db, [block.id])[block.id]
    assert [i.image_s3_url for i in images] == WORKS
    assert [i.image_s3_url for i in images if i.is_pick] == [PICK]


def test_block_without_description_is_not_dropped(db, regular_user):
    """Описание у сравнения необязательно: блок с работами, но без текста,
    раньше падал бы в общую проверку `body` и молча пропадал."""
    task = _task(db, regular_user)
    block = _compare_block(db, task, tail=False)

    assert block.block_type == BLOCK_COMPARE
    assert db.get(TaskBlock, block.id).body is None


def test_pick_is_cleared_on_other_gallery_types(db, regular_user):
    task = _task(db, regular_user)
    blocks = sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_PHOTO, "images": _images()},
    ])
    db.commit()

    images = get_images(db, [blocks[0].id])[blocks[0].id]
    assert not any(i.is_pick for i in images)


def _staff(client, user_factory, session_factory):
    user = user_factory(
        vk_id=660_100, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    client.cookies.set("session_id", session_factory(user).id)
    return user


def _save_material(client, monkeypatch, blocks):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: date.today())
    monkeypatch.setattr("app.services.program.today_msk", lambda: date.today())
    day = (date.today() + timedelta(days=3)).isoformat()
    return client.post(
        f"{PROGRAM}/{day}/material",
        json={"title": "Сравнение", "audience": EVERYONE, "blocks": blocks},
    )


def test_constructor_saves_compare_block(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory)

    resp = _save_material(client, monkeypatch, [_compare_item()])

    assert resp.status_code == 200, resp.text
    block = db.query(TaskBlock).filter(TaskBlock.block_type == BLOCK_COMPARE).one()
    picks = [i.image_s3_url for i in get_images(db, [block.id])[block.id] if i.is_pick]
    assert picks == [PICK]


def test_constructor_refuses_compare_without_pick(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory)

    resp = _save_material(client, monkeypatch, [_compare_item(pick=None)])

    assert resp.status_code == 422
    assert "свой выбор" in resp.text
    assert db.query(TaskBlock).count() == 0


def test_constructor_refuses_compare_with_two_picks(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory)
    item = _compare_item()
    item["images"][0]["is_pick"] = True

    resp = _save_material(client, monkeypatch, [item])

    assert resp.status_code == 422


def test_constructor_refuses_compare_with_one_work(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory)

    resp = _save_material(client, monkeypatch, [_compare_item(works=WORKS[:1], pick=WORKS[0])])

    assert resp.status_code == 422
    assert "две работы" in resp.text


def test_copy_keeps_the_pick(db, regular_user):
    """Копия задания в другой день без отметки показала бы каждому ученику
    «Не совпало»."""
    task = _task(db, regular_user)
    _compare_block(db, task, tail=False)
    copy = _task(db, regular_user, title="Копия")

    copy_task_blocks(db, from_task_id=task.id, to_task_id=copy.id)
    db.commit()

    cloned = db.query(TaskBlock).filter(TaskBlock.task_id == copy.id).one()
    picks = [i.image_s3_url for i in get_images(db, [cloned.id])[cloned.id] if i.is_pick]
    assert picks == [PICK]


# ── ученик ──────────────────────────────────────────────────────────────────

def test_pick_does_not_reach_the_student_before_answer(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)

    raw = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").text
    item = _feed_item(client, task.id, block.id)

    assert "is_pick" not in raw
    assert "pick_url" not in item
    assert "matched" not in item
    assert [i["url"] for i in item["images"]] == WORKS
    assert item["submit_endpoint"] == f"/cabinet/tracker/blocks/{block.id}/compare"


def test_matching_choice(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)

    resp = _choose(client, block.id, PICK)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "matched": True, "pick_url": PICK}
    item = _feed_item(client, task.id, block.id)
    assert item["chosen_url"] == PICK
    assert item["matched"] is True
    assert item["submit_endpoint"] is None


def test_other_choice_shows_the_teachers_work(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)

    resp = _choose(client, block.id, WORKS[0])

    assert resp.json()["matched"] is False
    assert resp.json()["pick_url"] == PICK
    item = _feed_item(client, task.id, block.id)
    assert item["matched"] is False
    assert item["pick_url"] == PICK


def test_one_attempt(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)
    _choose(client, block.id, WORKS[0])

    resp = _choose(client, block.id, PICK)

    assert resp.status_code == 409
    answers = db.query(TaskBlockAnswer).filter(TaskBlockAnswer.block_id == block.id).all()
    assert [a.text for a in answers] == [WORKS[0]]


def test_foreign_url_is_refused(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)

    resp = _choose(client, block.id, "https://evil.example/fake.jpg")

    assert resp.status_code == 422
    assert get_state(db, block_id=block.id, user_id=user.id) is None


def test_choice_after_deadline_is_refused(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)
    block.submit_until = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    item = _feed_item(client, task.id, block.id)
    resp = _choose(client, block.id, PICK)

    assert item["submit_endpoint"] is None
    assert item["edit_reason"]
    assert resp.status_code == 409


def test_choice_closes_the_block_and_opens_the_tail(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    _compare_block(db, task)
    block = db.query(TaskBlock).filter(TaskBlock.block_type == BLOCK_COMPARE).one()

    assert _statuses(db, user) == ["current", "locked"]
    _choose(client, block.id, WORKS[1])

    assert _statuses(db, user) == ["done", "current"]


def test_generic_answer_route_does_not_accept_compare(auth_client, db):
    """Обходом через общий роут ответов блок не закрыть: тот принимает только
    вопросы, шкалы и правила."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)

    resp = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "text": PICK}]},
    )

    assert resp.status_code in (404, 422)
    assert get_state(db, block_id=block.id, user_id=user.id) is None


def test_answer_survives_block_resave(auth_client, db):
    """`_sync_images` пересоздаёт картинки с новыми id — ответ по URL при этом
    остаётся на своей работе. Сами id здесь не сравниваем: SQLite в тестах
    выдаёт после удаления те же номера, Postgres — новые."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)
    _choose(client, block.id, PICK)

    sync_blocks(db, task_id=task.id, items=[
        _compare_item(block_id=block.id),
        {"block_type": BLOCK_TEXT, "body": "Разбор работ"},
    ])
    db.commit()

    item = _feed_item(client, task.id, block.id)
    assert item["chosen_url"] == PICK
    assert item["matched"] is True


# ── экран проверки ──────────────────────────────────────────────────────────

def test_review_queue_shows_work_numbers(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)
    _choose(client, block.id, WORKS[0])

    rows = [row for row in review_queue(db, student_id=user.id) if row["task_id"] == task.id]

    assert len(rows) == 1
    assert rows[0]["question"] == "Выбери работу, которая наберёт больше баллов"
    assert rows[0]["chosen"] == ["Работа №1"]
    assert rows[0]["correct"] == ["Работа №3"]
    # Адрес картинки преподавателю ничего не скажет — в текст не попадает.
    assert rows[0]["text"] == ""


def test_review_queue_survives_removed_work(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _compare_block(db, task)
    _choose(client, block.id, WORKS[0])

    sync_blocks(db, task_id=task.id, items=[
        _compare_item(block_id=block.id, works=WORKS[1:]),
    ])
    db.commit()

    row = next(r for r in review_queue(db, student_id=user.id) if r["task_id"] == task.id)
    assert row["chosen"] == ["Работа убрана из задания"]
    assert row["correct"] == ["Работа №2"]
    assert db.query(TaskBlockImage).filter(TaskBlockImage.block_id == block.id).count() == 4
    assert db.get(TrackerTask, task.id) is not None
