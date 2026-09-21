"""Starter profile: all combinations and the complete student journey."""

from datetime import timedelta
from itertools import product
import re

from app.models.task_block import TaskBlock
from app.models.tracker import ITEM_ARCHI_PROFILE, TrackerTask
from app.services.archi_profile import COMBINATIONS, PROFILES, result_for_answers
from app.services.cycle_feed import build_cycle_feed
from app.services.tz import today_msk


def test_every_three_answer_combination_has_one_profile():
    assert set(COMBINATIONS) == {"".join(p) for p in product("123", repeat=3)}
    assert set(COMBINATIONS.values()) == set(PROFILES)
    assert COMBINATIONS["121"] == "synthetic"
    assert COMBINATIONS["213"] == "analyst"
    assert COMBINATIONS["123"] == "provocateur"


def test_diagnostic_creates_questions_shows_result_and_gates_next_step(
    client, db, user_factory, session_factory
):
    admin = user_factory(vk_id=887_001, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    today = today_msk()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={
            "title": "Старт", "description": None,
            "starts_on": today.isoformat(),
            "ends_on": (today + timedelta(days=5)).isoformat(),
            "is_published": True,
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert cycle.status_code == 200, cycle.text
    cycle_id = cycle.json()["cycle_id"]
    cycle_page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert cycle_page.status_code == 200
    add_row = r'<div class="prg-actions prg-blocks-add">(?:(?!</div>).)*data-add-diagnostic'
    assert re.search(add_row, cycle_page.text, re.S)
    assert 'data-open-diagnostic' not in cycle_page.text
    day_page = client.get(f"/cabinet/staff/program/{(today + timedelta(days=1)).isoformat()}")
    assert day_page.status_code == 200
    assert re.search(add_row, day_page.text, re.S)
    assert day_page.text.count('data-add-diagnostic') >= 3
    assert 'data-open-form="archi_profile"' not in day_page.text
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/archi_profile",
        json={"title": "Диагностика АРХИ-ПРОФИЛЯ", "is_required": False, "blocks": []},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    assert db.get(TrackerTask, task_id).kind == ITEM_ARCHI_PROFILE
    assert db.get(TrackerTask, task_id).is_required is True
    assert db.query(TaskBlock).filter_by(task_id=task_id).count() == 3

    student = user_factory(vk_id=887_002, name="Ученик", role_name="ученик")
    feed = build_cycle_feed(
        db, user_id=student.id, user_tariff=student.tariff,
        start=today, end=today + timedelta(days=5), topic_id=cycle_id,
    )
    diagnostic_steps = [step for step in feed if step["task"].id == task_id]
    assert len(diagnostic_steps) == 3
    assert [step["status"] for step in diagnostic_steps] == ["current"] * 3
    client.cookies.set("session_id", session_factory(student).id)
    endpoint = f"/cabinet/tracker/tasks/{task_id}/blocks"
    question_data = client.get(endpoint)
    assert question_data.status_code == 200, question_data.text
    blocks = question_data.json()["blocks"]
    assert len(blocks) == 3
    assert question_data.json()["archi_profile"] is None
    premature = client.post(
        f"/cabinet/tracker/tasks/{task_id}/toggle", headers={"X-CSRF-Token": "x"}
    )
    assert premature.status_code == 409
    answers = [
        {"block_id": block["id"], "option_ids": [block["options"][digit - 1]["id"]]}
        for block, digit in zip(blocks, (1, 2, 1))
    ]
    invalid = client.post(endpoint, json={"answers": [{**answers[0], "option_ids": [
        blocks[0]["options"][0]["id"], blocks[0]["options"][1]["id"]
    ]}]}, headers={"X-CSRF-Token": "x"})
    assert invalid.status_code == 422
    incomplete = client.post(endpoint, json={"answers": answers[:2]}, headers={"X-CSRF-Token": "x"})
    assert incomplete.status_code == 422
    assert client.get(endpoint).json()["archi_profile"] is None
    saved = client.post(endpoint, json={"answers": answers}, headers={"X-CSRF-Token": "x"})
    assert saved.status_code == 200, saved.text
    changed = client.post(endpoint, json={"answers": [answers[0]]}, headers={"X-CSRF-Token": "x"})
    assert changed.status_code in (409, 422)
    result = client.get(endpoint).json()["archi_profile"]
    assert result["combination"] == "121"
    assert result["title"] == "Архитектор-синтетик"
    assert result_for_answers(db, task_id, student.id)["combination"] == "121"
    personal = client.get("/cabinet/personal")
    assert personal.status_code == 200
    assert "Архитектор-синтетик" in personal.text
    assert "121" in personal.text
    client.cookies.set("session_id", session_factory(admin).id)
    removed = client.post(
        f"/cabinet/staff/program/items/{task_id}/delete",
        json={}, headers={"X-CSRF-Token": "x"},
    )
    assert removed.status_code == 200
    client.cookies.set("session_id", session_factory(student).id)
    assert "Архитектор-синтетик" in client.get("/cabinet/personal").text


def test_teacher_authored_diagnostic_maps_all_combinations(client, db, user_factory, session_factory):
    admin = user_factory(vk_id=887_101, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    today = today_msk()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Новый цикл", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    )
    assert cycle.status_code == 200
    config = {
        "questions": [
            {"text": "Что важнее?", "options": [{"text": "Свет", "value": "1"}, {"text": "Форма", "value": "2"}]},
            {"text": "Что ближе?", "options": [{"text": "Дом", "value": "A"}, {"text": "Город", "value": "B"}]},
        ],
        "results": [
            {"title": "Исследователь", "text": "Ты ищешь связи.", "combinations": [["1", "A"], ["2", "B"]]},
            {"title": "Создатель", "text": "Ты создаёшь формы.", "combinations": [["1", "B"], ["2", "A"]]},
        ],
    }
    incomplete = {**config, "results": config["results"][:1]}
    rejected = client.post(
        f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/archi_profile",
        json={"title": "Профиль", "description": "Выбери ответы", "diagnostic": incomplete},
        headers={"X-CSRF-Token": "x"},
    )
    assert rejected.status_code == 422
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/archi_profile",
        json={"title": "Профиль", "description": "Выбери ответы", "diagnostic": config},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    assert db.get(TrackerTask, task_id).diagnostic_config == config

    student = user_factory(vk_id=887_102, name="Ученик", role_name="ученик")
    client.cookies.set("session_id", session_factory(student).id)
    endpoint = f"/cabinet/tracker/tasks/{task_id}/blocks"
    blocks = client.get(endpoint).json()["blocks"]
    assert len(blocks) == 2
    assert [len(block["options"]) for block in blocks] == [2, 2]
    answers = [
        {"block_id": blocks[0]["id"], "option_ids": [blocks[0]["options"][1]["id"]]},
        {"block_id": blocks[1]["id"], "option_ids": [blocks[1]["options"][0]["id"]]},
    ]
    assert client.post(endpoint, json={"answers": answers[:1]}, headers={"X-CSRF-Token": "x"}).status_code == 422
    assert client.post(endpoint, json={"answers": answers}, headers={"X-CSRF-Token": "x"}).status_code == 200
    result = client.get(endpoint).json()["archi_profile"]
    assert result["title"] == "Создатель"
    assert result["combination"] == "2A"
    assert "Ты создаёшь формы." in client.get("/cabinet/personal").text
    client.cookies.set("session_id", session_factory(admin).id)
    changed_config = {**config, "questions": [{**config["questions"][0], "text": "Новый вопрос"}, config["questions"][1]]}
    changed = client.post(
        f"/cabinet/staff/program/items/{task_id}/archi_profile",
        json={"title": "Профиль", "description": "Выбери ответы", "diagnostic": changed_config},
        headers={"X-CSRF-Token": "x"},
    )
    assert changed.status_code == 409
    assert db.get(TrackerTask, task_id).diagnostic_config == config
