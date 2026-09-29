"""«Взять содержимое из другого задания» на экране заданий цикла и этапа
(владелец 29.09.2026).

Сценарий: в новом этапе заводят новое задание «Портфолио» и одним нажатием
забирают блоки старого. Переиспользовать само старое задание нельзя: личное
окно загрузки на 72 часа помнится за парой «ученик + блок», и у кого оно уже
открывалось, тому тот же блок загрузку не откроет. Новые блоки — новое окно.

Что держат тесты:
- бездатное задание этапа попадает в список источников, даже когда заданий
  дня с содержимым больше 50 (раньше `nullslast` отрезал его первым), и
  подписано названием этапа, а не днём;
- поиск по названию находит его;
- копия несёт длительность окна и отметку «выбор преподавателя», а
  абсолютных сроков не несёт — копия старых дат закрыла бы новое задание сразу;
- экран цикла отдаёт кнопку, поле поиска и функции переноса.
"""

from datetime import datetime, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_STAGE, LearningTopic
from app.models.task_block import (
    BLOCK_COMPARE,
    BLOCK_PORTFOLIO,
    BLOCK_TEXT,
    TaskBlock,
)
from app.models.tracker import TrackerTask
from app.services.task_blocks import get_images
from app.services.tz import today_msk

PROGRAM = "/cabinet/staff/program"
CSRF = {"X-CSRF-Token": "x"}

WORKS = [f"https://s3/works/{n}.jpg" for n in range(1, 4)]
PICK = WORKS[1]


def _login_chief(client, user_factory, session_factory):
    chief = user_factory(vk_id=961_001, name="Главный", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(chief).id)
    return chief


def _stage(client, db, title, *, starts_in=0):
    starts = today_msk() + timedelta(days=starts_in)
    resp = client.post(f"{PROGRAM}/stages", json={
        "title": title, "description": None,
        "starts_on": starts.isoformat(), "ends_on": (starts + timedelta(days=30)).isoformat(),
        "is_published": True,
    }, headers=CSRF)
    assert resp.status_code == 200, resp.text
    return (
        db.query(LearningTopic)
        .filter(LearningTopic.kind == TOPIC_KIND_STAGE, LearningTopic.title == title)
        .one()
    )


def _portfolio_blocks():
    return [
        {"block_type": BLOCK_PORTFOLIO, "title": "Загрузите работы «До»", "body": None,
         "is_required": True, "subject": None, "tariffs": [],
         "is_required_for_intake": False, "opens_at": None, "bypass_sequence": False,
         "portfolio_window_hours": 72},
        {"block_type": BLOCK_COMPARE, "title": "Какая работа наберёт больше?",
         "is_required": True,
         "images": [
             {"url": url, "path": url.rsplit("/", 1)[-1], "is_pick": url == PICK}
             for url in WORKS
         ]},
    ]


def _create_item(client, topic_id, title, blocks):
    resp = client.post(f"{PROGRAM}/cycles/{topic_id}/items/material", json={
        "title": title, "description": None, "subject": None,
        "is_required": True, "starts_on": None, "blocks": blocks,
    }, headers=CSRF)
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def _dated_tasks_with_content(db, owner_id, count):
    """Задания дня с блоками, в прошлом и в будущем — как в календаре."""
    base = datetime.now(timezone.utc)
    for n in range(count):
        task = TrackerTask(
            title=f"День {n}", kind="material", created_by_id=owner_id,
            due_at=base + timedelta(days=n - count // 2), assign_to_all=True,
        )
        db.add(task)
        db.flush()
        db.add(TaskBlock(task_id=task.id, sort_order=0, block_type=BLOCK_TEXT, body="Текст"))
    db.commit()


def test_undated_stage_task_is_offered_even_behind_fifty_dated(
    client, db, user_factory, session_factory
):
    chief = _login_chief(client, user_factory, session_factory)
    stage = _stage(client, db, "Предобучение")
    portfolio_id = _create_item(client, stage.id, "Портфолио", _portfolio_blocks())
    _dated_tasks_with_content(db, chief.id, 55)

    items = client.get(f"{PROGRAM}/blocks-source").json()["items"]

    assert len(items) == 50
    first = items[0]
    assert first["id"] == portfolio_id
    # У задания этапа дня нет — подпись по рамке.
    assert first["day"] is None
    assert first["frame"] == "Предобучение"
    # Задания дня идут за ним по дню, как раньше: свежий день выше.
    days = [i["day"] for i in items[1:]]
    assert days == sorted(days, reverse=True)
    assert all(i["frame"] is None for i in items[1:])


def test_search_finds_stage_task_by_title(client, db, user_factory, session_factory):
    chief = _login_chief(client, user_factory, session_factory)
    stage = _stage(client, db, "Предобучение")
    portfolio_id = _create_item(client, stage.id, "Портфолио", _portfolio_blocks())
    _dated_tasks_with_content(db, chief.id, 3)

    items = client.get(f"{PROGRAM}/blocks-source", params={"q": "Портф"}).json()["items"]

    assert [i["id"] for i in items] == [portfolio_id]


def test_copy_into_new_stage_keeps_window_and_pick_but_not_dates(
    client, db, user_factory, session_factory
):
    """Путь кнопки целиком: взять блоки старого «Портфолио» и сохранить их
    новым заданием нового этапа — так, как это делает форма."""
    _login_chief(client, user_factory, session_factory)
    old_stage = _stage(client, db, "Предобучение")
    new_stage = _stage(client, db, "Годовой курс", starts_in=31)
    source_id = _create_item(client, old_stage.id, "Портфолио", _portfolio_blocks())
    closes_at = datetime.now(timezone.utc) + timedelta(days=3)
    for block in db.query(TaskBlock).filter(TaskBlock.task_id == source_id):
        block.closes_at = closes_at
    db.commit()

    blocks = client.get(f"{PROGRAM}/blocks-source/{source_id}").json()["blocks"]

    assert [b["block_type"] for b in blocks] == [BLOCK_PORTFOLIO, BLOCK_COMPARE]
    assert blocks[0]["portfolio_window_hours"] == 72
    assert [i["url"] for i in blocks[1]["images"] if i["is_pick"]] == [PICK]
    for block in blocks:
        assert "id" not in block
        for field in ("opens_at", "closes_at", "submit_until"):
            assert field not in block

    copy_id = _create_item(client, new_stage.id, "Портфолио", blocks)

    copied = (
        db.query(TaskBlock).filter(TaskBlock.task_id == copy_id)
        .order_by(TaskBlock.sort_order).all()
    )
    source_ids = {
        b.id for b in db.query(TaskBlock).filter(TaskBlock.task_id == source_id)
    }
    assert not source_ids & {b.id for b in copied}
    assert copied[0].portfolio_window_hours == 72
    assert all(b.closes_at is None for b in copied)
    picks = [i.image_s3_url for i in get_images(db, [copied[1].id])[copied[1].id] if i.is_pick]
    assert picks == [PICK]


def test_cycle_items_page_has_take_blocks_controls(client, db, user_factory, session_factory):
    _login_chief(client, user_factory, session_factory)
    stage = _stage(client, db, "Годовой курс")

    page = client.get(f"{PROGRAM}/cycles/{stage.id}").text

    assert "data-take-blocks>Взять содержимое из другого задания</button>" in page
    assert "data-take-search" in page
    assert "<select data-take-source" in page
    assert "function fillTakeSources(" in page
    assert "function takeBlocksFrom(" in page
    assert "searchTakeSources(event.target)" in page
