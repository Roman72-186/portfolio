"""Диагностика как блок внутри обычного «Задания» (владелец 24.09.2026,
второй способ рядом с отдельным видом archi_profile) — доступность
настраивается так же, как у остальных материалов, вопросы диагностики
запираются поблочно, не всей формой.

Второй раунд (владелец 24.09.2026, тот же день): диагностика — одна строка
`block_type == "diagnostic"` среди обычных блоков конструктора, на своём
месте в порядке, с той же панелью «Доступность блока» (тариф, даты,
«Блокирует дальнейшую выдачу»), что и у любого другого типа."""

from datetime import timedelta

from app.api.cabinet_program import _edit_payloads
from app.models.task_block import TaskBlock, TaskBlockResponse, TaskBlockTariff
from app.models.tracker import ITEM_MATERIAL, TrackerTask
from app.services.tz import today_msk

CONFIG = {
    # `title`/`intro` — те же generic-поля блока, что у любого другого типа
    # (владелец 24.09.2026, третий раунд); тесты ниже их не задают, поэтому
    # `validate_diagnostic_config` возвращает их пустыми — это и есть форма
    # хранения `TrackerTask.diagnostic_config`, с которой нужно сравнивать.
    "title": None, "intro": None,
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


def _diagnostic_block(config=CONFIG, **availability):
    """Строка `block_type == "diagnostic"` для `blocks` в payload — тот же
    контракт, что теперь шлёт конструктор: одна строка со своими настройками
    доступности, а не отдельное верхнеуровневое поле `diagnostic`."""
    item = {"block_type": "diagnostic", "diagnostic": config}
    item.update(availability)
    return item


def _make_cycle(client, today):
    resp = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Цикл", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["cycle_id"]


def test_material_task_carries_diagnostic_blocks_alongside_regular_ones(
    client, db, user_factory, session_factory
):
    admin = user_factory(vk_id=889_001, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())

    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание с диагностикой", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{"block_type": "text", "body": "Прочитай перед началом"}, _diagnostic_block()],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    task = db.get(TrackerTask, task_id)
    assert task.kind == ITEM_MATERIAL
    assert task.diagnostic_config == CONFIG

    blocks = db.query(TaskBlock).filter_by(task_id=task_id).order_by(TaskBlock.sort_order).all()
    assert [b.block_type for b in blocks] == ["text", "question", "question"]
    assert [b.is_diagnostic for b in blocks] == [False, True, True]

    # Форма правки не должна показывать отдельные блоки-вопросы диагностики в
    # общем редакторе — только одну синтетическую строку `diagnostic`, на
    # своём месте среди обычных блоков (владелец 24.09.2026: «должна
    # добавляться по порядку с остальными заданиями»).
    payload = _edit_payloads(db, [task], {})[task_id]
    assert [b["block_type"] for b in payload["blocks"]] == ["text", "diagnostic"]
    assert payload["blocks"][1]["diagnostic"] == CONFIG
    assert "diagnostic" not in payload


def test_diagnostic_sits_in_order_and_shares_availability_filters(
    client, db, user_factory, session_factory
):
    """Владелец 24.09.2026, второй раунд: диагностика должна вставать по
    месту среди остальных блоков (не всегда в конце) и получать те же
    фильтры доступности — тариф, «Блокирует дальнейшую выдачу» — что и любая
    другая кнопка, применённые одинаково ко всем её вопросам."""
    admin = user_factory(vk_id=889_051, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())

    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [
                {"block_type": "text", "body": "Перед диагностикой"},
                _diagnostic_block(is_required=True, tariffs=["УВЕРЕННЫЙ"]),
                {"block_type": "text", "body": "После диагностики"},
            ],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    blocks = db.query(TaskBlock).filter_by(task_id=task_id).order_by(TaskBlock.sort_order).all()
    # Диагностика — ровно между двумя текстовыми блоками, не в хвосте списка.
    assert [b.block_type for b in blocks] == ["text", "question", "question", "text"]
    assert blocks[0].body == "Перед диагностикой"
    assert blocks[3].body == "После диагностики"
    # Обе диагностических строки получили одинаковые фильтры доступности —
    # не только последняя, как было до этого раунда.
    for question_block in blocks[1:3]:
        assert question_block.is_required is True
        tariffs = {
            row.tariff for row in
            db.query(TaskBlockTariff).filter_by(block_id=question_block.id).all()
        }
        assert tariffs == {"УВЕРЕННЫЙ"}


def test_diagnostic_locks_per_block_not_the_whole_form(client, db, user_factory, session_factory):
    """Подтверждённое владельцем решение: ответ на диагностику запирает
    только её саму, несвязанный вопрос того же задания остаётся доступен."""
    admin = user_factory(vk_id=889_101, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())

    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание с диагностикой и опросом", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [
                {"block_type": "question", "body": "Свой вопрос куратора", "question_type": "text"},
                _diagnostic_block(),
            ],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    student = user_factory(vk_id=889_102, name="Ученик", role_name="ученик")
    client.cookies.set("session_id", session_factory(student).id)
    endpoint = f"/cabinet/tracker/tasks/{task_id}/blocks"

    got = client.get(endpoint)
    assert got.status_code == 200, got.text
    blocks = got.json()["blocks"]
    diagnostic_blocks = [b for b in blocks if b["block_type"] == "question" and b.get("is_archi_profile")]
    own_question = [b for b in blocks if b["block_type"] == "question" and not b.get("is_archi_profile")]
    assert len(diagnostic_blocks) == 2
    assert len(own_question) == 1

    # Отвечаем на диагностику целиком — своя очередь, свой запрос.
    answers = [
        {"block_id": diagnostic_blocks[0]["id"], "option_ids": [diagnostic_blocks[0]["options"][0]["id"]]},
        {"block_id": diagnostic_blocks[1]["id"], "option_ids": [diagnostic_blocks[1]["options"][1]["id"]]},
    ]
    saved = client.post(endpoint, json={"answers": answers}, headers={"X-CSRF-Token": "x"})
    assert saved.status_code == 200, saved.text

    # Диагностика посчиталась.
    after = client.get(endpoint).json()
    assert after["archi_profile"]["title"] == "Создатель"
    assert after["archi_profile"]["combination"] == "1B"

    # Несвязанный вопрос куратора остаётся доступным для ответа — форма не
    # заперлась целиком.
    still_open = [b for b in after["blocks"] if b["id"] == own_question[0]["id"]][0]
    assert still_open["edit_reason"] is None
    answer_own = client.post(
        endpoint,
        json={"answers": [{"block_id": own_question[0]["id"], "text": "Мой ответ"}]},
        headers={"X-CSRF-Token": "x"},
    )
    assert answer_own.status_code == 200, answer_own.text

    # Повторная отправка диагностики отдельным вопросом — заперта (одна
    # попытка): все вопросы уже отвечены, submit требует ровно неотвеченный
    # остаток, а его больше нет.
    relock = client.post(
        endpoint,
        json={"answers": [{
            "block_id": diagnostic_blocks[0]["id"],
            "option_ids": [diagnostic_blocks[0]["options"][1]["id"]],
        }]},
        headers={"X-CSRF-Token": "x"},
    )
    assert relock.status_code == 422


def test_mixing_diagnostic_and_regular_answers_in_one_request_is_rejected(
    client, db, user_factory, session_factory
):
    admin = user_factory(vk_id=889_201, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [
                {"block_type": "question", "body": "Свой вопрос", "question_type": "text"},
                _diagnostic_block(),
            ],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    student = user_factory(vk_id=889_202, name="Ученик", role_name="ученик")
    client.cookies.set("session_id", session_factory(student).id)
    endpoint = f"/cabinet/tracker/tasks/{task_id}/blocks"
    blocks = client.get(endpoint).json()["blocks"]
    diagnostic_block = next(b for b in blocks if b.get("is_archi_profile"))
    own_question = next(b for b in blocks if not b.get("is_archi_profile"))

    mixed = client.post(
        endpoint,
        json={"answers": [
            {"block_id": diagnostic_block["id"], "option_ids": [diagnostic_block["options"][0]["id"]]},
            {"block_id": own_question["id"], "text": "Мой ответ"},
        ]},
        headers={"X-CSRF-Token": "x"},
    )
    assert mixed.status_code == 422


def test_removing_diagnostic_row_deletes_it_when_unanswered(
    client, db, user_factory, session_factory
):
    """Строки диагностики нет среди присланных блоков — куратор её убрал
    (владелец 24.09.2026, второй раунд: строка ведёт себя как любой другой
    блок — раз её нет в списке, значит убрали). Разрешено, пока на
    диагностику никто не ответил."""
    admin = user_factory(vk_id=889_301, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{"block_type": "text", "body": "Старый текст"}, _diagnostic_block()],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    updated = client.post(
        f"/cabinet/staff/program/items/{task_id}/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{"block_type": "text", "body": "Новый текст"}],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert updated.status_code == 200, updated.text

    task = db.get(TrackerTask, task_id)
    assert task.diagnostic_config is None
    assert db.query(TaskBlock).filter_by(task_id=task_id, is_diagnostic=True).count() == 0


def test_removing_already_answered_diagnostic_row_is_rejected(
    client, db, user_factory, session_factory
):
    """Тот же случай, но ученик уже ответил — удалять нельзя (та же защита,
    что и у смены вопросов уже отвеченной диагностики)."""
    admin = user_factory(vk_id=889_302, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{"block_type": "text", "body": "Старый текст"}, _diagnostic_block()],
        },
        headers={"X-CSRF-Token": "x"},
    )
    task_id = created.json()["task_id"]
    diagnostic_block_ids_before = sorted(
        b.id for b in db.query(TaskBlock).filter_by(task_id=task_id, is_diagnostic=True).all()
    )

    student = user_factory(vk_id=889_303, name="Ученик", role_name="ученик")
    client.cookies.set("session_id", session_factory(student).id)
    endpoint = f"/cabinet/tracker/tasks/{task_id}/blocks"
    blocks = client.get(endpoint).json()["blocks"]
    diagnostic_blocks = [b for b in blocks if b.get("is_archi_profile")]
    answers = [
        {"block_id": b["id"], "option_ids": [b["options"][0]["id"]]} for b in diagnostic_blocks
    ]
    saved = client.post(endpoint, json={"answers": answers}, headers={"X-CSRF-Token": "x"})
    assert saved.status_code == 200, saved.text

    client.cookies.set("session_id", session_factory(admin).id)
    updated = client.post(
        f"/cabinet/staff/program/items/{task_id}/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{"block_type": "text", "body": "Новый текст"}],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert updated.status_code == 409, updated.text

    task = db.get(TrackerTask, task_id)
    assert task.diagnostic_config == CONFIG
    diagnostic_block_ids_after = sorted(
        b.id for b in db.query(TaskBlock).filter_by(task_id=task_id, is_diagnostic=True).all()
    )
    assert diagnostic_block_ids_after == diagnostic_block_ids_before


def test_editing_with_diagnostic_row_resent_keeps_it_and_updates_regular_blocks(
    client, db, user_factory, session_factory
):
    """Реальный UI всегда перечитывает форму из `_edit_payloads` и шлёт её
    целиком обратно, включая синтетическую строку диагностики — этот путь и
    должен быть основным, предыдущий тест проверяет только защиту от потери
    данных при частичной отправке."""
    admin = user_factory(vk_id=889_351, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{"block_type": "text", "body": "Старый текст"}, _diagnostic_block()],
        },
        headers={"X-CSRF-Token": "x"},
    )
    task_id = created.json()["task_id"]
    task = db.get(TrackerTask, task_id)
    diagnostic_ids_before = sorted(
        b.id for b in db.query(TaskBlock).filter_by(task_id=task_id, is_diagnostic=True).all()
    )

    payload = _edit_payloads(db, [task], {})[task_id]
    payload["blocks"][0]["body"] = "Новый текст"
    updated = client.post(
        f"/cabinet/staff/program/items/{task_id}/material",
        json={
            "title": payload["title"], "description": None, "subject": None,
            "is_required": True, "starts_on": None, "blocks": payload["blocks"],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert updated.status_code == 200, updated.text

    db.expire_all()
    task = db.get(TrackerTask, task_id)
    assert task.diagnostic_config == CONFIG
    diagnostic_ids_after = sorted(
        b.id for b in db.query(TaskBlock).filter_by(task_id=task_id, is_diagnostic=True).all()
    )
    assert diagnostic_ids_after == diagnostic_ids_before
    text_blocks = db.query(TaskBlock).filter_by(task_id=task_id, is_diagnostic=False).all()
    assert [b.body for b in text_blocks] == ["Новый текст"]


def test_diagnostic_result_shows_on_personal_page_for_embedded_diagnostic(
    client, db, user_factory, session_factory
):
    admin = user_factory(vk_id=889_401, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Материал с диагностикой", "description": None, "subject": None,
            "is_required": True, "starts_on": None, "blocks": [_diagnostic_block()],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    student = user_factory(vk_id=889_402, name="Ученик", role_name="ученик")
    client.cookies.set("session_id", session_factory(student).id)
    endpoint = f"/cabinet/tracker/tasks/{task_id}/blocks"
    blocks = client.get(endpoint).json()["blocks"]
    answers = [
        {"block_id": blocks[0]["id"], "option_ids": [blocks[0]["options"][0]["id"]]},
        {"block_id": blocks[1]["id"], "option_ids": [blocks[1]["options"][0]["id"]]},
    ]
    saved = client.post(endpoint, json={"answers": answers}, headers={"X-CSRF-Token": "x"})
    assert saved.status_code == 200, saved.text

    personal = client.get("/cabinet/personal")
    assert personal.status_code == 200
    assert "Исследователь" in personal.text


def test_diagnostic_title_and_body_are_generic_block_fields(
    client, db, user_factory, session_factory
):
    """Владелец 24.09.2026, третий раунд: «добавить пункты Название,
    описание, аналогично настройки остальным блокам» — те же generic-поля
    блока (`data-b-title`/`data-b-body`), что и у любого другого типа,
    хранятся внутри `diagnostic_config`, показываются ученику один раз перед
    первым вопросом диагностики и переживают правку без диагностики."""
    admin = user_factory(vk_id=889_501, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    cycle_id = _make_cycle(client, today_msk())

    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{
                "block_type": "diagnostic", "diagnostic": {**CONFIG, "title": None, "intro": None},
                "title": "Диагностика архитектурного профиля",
                "body": "Ответь на пару вопросов — узнаешь свой профиль.",
            }],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    task = db.get(TrackerTask, task_id)
    assert task.diagnostic_config["title"] == "Диагностика архитектурного профиля"
    assert task.diagnostic_config["intro"] == "Ответь на пару вопросов — узнаешь свой профиль."

    # Форма правки подхватывает их в ту же строку, что вопросы/результаты.
    payload = _edit_payloads(db, [task], {})[task_id]
    diagnostic_entry = payload["blocks"][0]
    assert diagnostic_entry["title"] == "Диагностика архитектурного профиля"
    assert diagnostic_entry["body"] == "Ответь на пару вопросов — узнаешь свой профиль."

    # Ученику показывается один раз, на первом вопросе диагностики.
    student = user_factory(vk_id=889_502, name="Ученик", role_name="ученик")
    client.cookies.set("session_id", session_factory(student).id)
    blocks = client.get(f"/cabinet/tracker/tasks/{task_id}/blocks").json()["blocks"]
    assert blocks[0]["diagnostic_intro_title"] == "Диагностика архитектурного профиля"
    assert "Ответь на пару вопросов" in blocks[0]["diagnostic_intro_body_html"]
    assert "diagnostic_intro_title" not in blocks[1]

    # Название/описание — не вопросы/результаты, правятся даже после ответа.
    answers = [
        {"block_id": b["id"], "option_ids": [b["options"][0]["id"]]} for b in blocks
    ]
    saved = client.post(
        f"/cabinet/tracker/tasks/{task_id}/blocks", json={"answers": answers},
        headers={"X-CSRF-Token": "x"},
    )
    assert saved.status_code == 200, saved.text

    client.cookies.set("session_id", session_factory(admin).id)
    updated = client.post(
        f"/cabinet/staff/program/items/{task_id}/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{
                "block_type": "diagnostic", "diagnostic": {**CONFIG, "title": None, "intro": None},
                "title": "Новое название", "body": "Новое описание.",
            }],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert updated.status_code == 200, updated.text
    db.expire_all()
    task = db.get(TrackerTask, task_id)
    assert task.diagnostic_config["title"] == "Новое название"
    assert task.diagnostic_config["intro"] == "Новое описание."
