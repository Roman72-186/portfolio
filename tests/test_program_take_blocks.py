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

С 02.10.2026 (владелец):
- задания удалённого цикла или этапа не предлагаются — «если удалили, значит
  они не нужны»;
- поиск находит задание и по названию его цикла или этапа;
- диагностика переносится одной строкой со своими результатами, а не
  отдельными вопросами;
- у карточки задания в конструкторе есть ID, который копируется нажатием.
"""

from datetime import datetime, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
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


def _cycle_in(db, stage, title):
    """Цикл этапа напрямую в базе: форма цикла здесь ни при чём."""
    cycle = LearningTopic(
        title=title, kind=TOPIC_KIND_WEEK, parent_id=stage.id,
        opens_at=datetime.now(timezone.utc), is_published=True,
    )
    db.add(cycle)
    db.commit()
    return cycle


def _text_blocks(body="Текст"):
    return [{"block_type": BLOCK_TEXT, "title": None, "body": body}]


def test_tasks_of_deleted_cycle_or_stage_are_not_offered(
    client, db, user_factory, session_factory
):
    """На проде 02.10.2026 8 из 15 источников были из удалённых тестовых
    циклов и ничем не отличались от нужных."""
    _login_chief(client, user_factory, session_factory)
    live = _stage(client, db, "Предобучение")
    live_id = _create_item(client, live.id, "Портфолио", _text_blocks())
    gone_stage = _stage(client, db, "Тестовый этап")
    in_gone_stage = _create_item(client, gone_stage.id, "Портфолио старое", _text_blocks())
    cycle_of_gone_stage = _cycle_in(db, gone_stage, "Цикл 1")
    in_cycle_of_gone_stage = _create_item(
        client, cycle_of_gone_stage.id, "Эскизы", _text_blocks()
    )
    gone_cycle = _cycle_in(db, live, "тест")
    in_gone_cycle = _create_item(client, gone_cycle.id, "орьорьро", _text_blocks())
    now = datetime.now(timezone.utc)
    gone_stage.deleted_at = now
    gone_cycle.deleted_at = now
    db.commit()

    ids = [i["id"] for i in client.get(f"{PROGRAM}/blocks-source").json()["items"]]

    assert ids == [live_id]
    for hidden in (in_gone_stage, in_cycle_of_gone_stage, in_gone_cycle):
        assert hidden not in ids


def test_search_finds_task_by_its_cycle_and_stage_title(
    client, db, user_factory, session_factory
):
    """В подписи видно «Портфолио · Предобучение» — по «Предобучение» задание
    должно находиться, хотя в его собственном названии этого слова нет."""
    _login_chief(client, user_factory, session_factory)
    stage = _stage(client, db, "Предобучение")
    portfolio_id = _create_item(client, stage.id, "Портфолио", _text_blocks())
    cycle = _cycle_in(db, stage, "Формообразование")
    nodes_id = _create_item(client, cycle.id, "Узлы", _text_blocks())
    other = _stage(client, db, "Годовой курс")
    _create_item(client, other.id, "Чужое", _text_blocks())

    # С заглавной: SQLite в тестах регистр кириллицы в ILIKE не сворачивает,
    # боевой Postgres (en_US.utf8) сворачивает — там «предоб» тоже найдётся.
    by_stage = client.get(f"{PROGRAM}/blocks-source", params={"q": "Предоб"}).json()["items"]
    by_cycle = client.get(f"{PROGRAM}/blocks-source", params={"q": "Формообраз"}).json()["items"]

    assert sorted(i["id"] for i in by_stage) == sorted([portfolio_id, nodes_id])
    assert [i["id"] for i in by_cycle] == [nodes_id]


DIAGNOSTIC = {
    "title": "Архитектурный профиль", "intro": "Пара вопросов о тебе.",
    "questions": [
        {"text": "Что важнее?", "options": [{"text": "Свет", "value": "1"}, {"text": "Форма", "value": "2"}]},
        {"text": "Что ближе?", "options": [{"text": "Дом", "value": "A"}, {"text": "Город", "value": "B"}]},
    ],
    "results": [
        {"title": "Исследователь", "text": "Ты ищешь связи.", "architects": "",
         "combinations": [["1", "A"], ["2", "B"]]},
        {"title": "Создатель", "text": "Ты создаёшь формы.", "architects": "",
         "combinations": [["1", "B"], ["2", "A"]]},
    ],
}


def test_diagnostic_is_copied_whole_with_its_results(
    client, db, user_factory, session_factory
):
    """До 02.10.2026 копия «Формообразования узлов» получала вопросы
    диагностики обычными вопросами, а результаты терялись."""
    _login_chief(client, user_factory, session_factory)
    stage = _stage(client, db, "Предобучение")
    source_id = _create_item(client, stage.id, "Узлы", _text_blocks("Вступление") + [{
        "block_type": "diagnostic", "diagnostic": DIAGNOSTIC,
        "title": DIAGNOSTIC["title"], "body": DIAGNOSTIC["intro"],
        "is_required": True, "tariffs": [],
    }])

    blocks = client.get(f"{PROGRAM}/blocks-source/{source_id}").json()["blocks"]

    assert [b["block_type"] for b in blocks] == [BLOCK_TEXT, "diagnostic"]
    diagnostic = blocks[1]
    assert diagnostic["title"] == "Архитектурный профиль"
    assert diagnostic["body"] == "Пара вопросов о тебе."
    assert diagnostic["is_required"] is True
    assert diagnostic["diagnostic"]["results"] == DIAGNOSTIC["results"]
    assert "id" not in diagnostic
    for field in ("opens_at", "closes_at", "submit_until"):
        assert field not in diagnostic

    copy_id = _create_item(client, stage.id, "Узлы, копия", blocks)

    source = db.get(TrackerTask, source_id)
    copy = db.get(TrackerTask, copy_id)
    db.refresh(copy)
    assert copy.diagnostic_config["questions"] == source.diagnostic_config["questions"]
    assert copy.diagnostic_config["results"] == source.diagnostic_config["results"]
    copied = db.query(TaskBlock).filter(TaskBlock.task_id == copy_id).all()
    assert sum(1 for b in copied if b.is_diagnostic) == len(DIAGNOSTIC["questions"])


def test_cycle_items_card_shows_copyable_task_id(client, db, user_factory, session_factory):
    _login_chief(client, user_factory, session_factory)
    stage = _stage(client, db, "Годовой курс")
    task_id = _create_item(client, stage.id, "Портфолио", _text_blocks())

    page = client.get(f"{PROGRAM}/cycles/{stage.id}").text

    assert f'data-copy-task-id="{task_id}"' in page
    assert f">ID {task_id}</button>" in page
    assert "function copyTaskId(" in page
