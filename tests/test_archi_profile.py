"""Starter profile: all combinations and the complete student journey."""

from datetime import timedelta
from itertools import product
import re

from app.api.cabinet_program import _edit_payloads
from app.models.task_block import TaskBlock, TaskBlockResponse
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
            {"title": "Исследователь", "text": "Ты ищешь связи.", "architects": "Ван Шу, Тадао Андо",
             "combinations": [["1", "A"], ["2", "B"]]},
            {"title": "Создатель", "text": "Ты **создаёшь** формы.", "architects": "Ле Корбюзье",
             "combinations": [["1", "B"], ["2", "A"]]},
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
    # Ручная стилизация формулы силы и реальных архитекторов (владелец
    # 22.09.2026: «стилизация, как в остальных блоках») — `*_html` рядом с
    # сырым текстом, тот же `format_rich_text`, что у тела остальных блоков.
    assert result["formula"] == "Ты **создаёшь** формы."
    assert result["formula_html"] == "Ты <strong>создаёшь</strong> формы."
    assert result["architects_html"] == "Ле Корбюзье"
    personal_page = client.get("/cabinet/personal").text
    assert "Ты <strong>создаёшь</strong> формы." in personal_page
    client.cookies.set("session_id", session_factory(admin).id)
    changed_config = {**config, "questions": [{**config["questions"][0], "text": "Новый вопрос"}, config["questions"][1]]}
    changed = client.post(
        f"/cabinet/staff/program/items/{task_id}/archi_profile",
        json={"title": "Профиль", "description": "Выбери ответы", "diagnostic": changed_config},
        headers={"X-CSRF-Token": "x"},
    )
    assert changed.status_code == 409
    assert db.get(TrackerTask, task_id).diagnostic_config == config


def test_editing_diagnostic_must_not_resend_its_own_question_blocks(
    client, db, user_factory, session_factory
):
    """Регрессия (жалоба Лизы 22.09.2026 — «У вопроса с вариантами отметьте
    хотя бы один верный ответ» повторялась по числу вопросов при сохранении).

    `archi_profile.blocks_from_config` заводит вопросы диагностики как
    обычные блоки-«Вопрос» в базе — так их видит ученик. `_edit_payloads`
    отдаёт форме правки блоки конструктора «у всех видов элемента без
    исключения», включая эти. Старый клиент подхватывал их в скрытый общий
    редактор блоков и отправлял обратно при сохранении — а у варианта
    диагностики нет и не может быть «верного ответа», поэтому
    `BlockItem.choice_question_needs_a_right_answer` отказывал на каждый
    вопрос. Сервер и так игнорирует `payload.blocks` для archi_profile
    (`_update_simple_item`: `elif kind != ITEM_ARCHI_PROFILE`), но Pydantic
    валидирует тело запроса раньше, чем эта ветка успевает сработать —
    чинить нужно на клиенте, не отправлять эти блоки вовсе."""
    admin = user_factory(vk_id=887_201, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    today = today_msk()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Цикл", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    )
    config = {
        "questions": [
            {"text": "Вопрос", "options": [{"text": "Свет", "value": "1"}, {"text": "Форма", "value": "2"}]},
        ],
        "results": [
            {"title": "Итог", "text": "Формула", "architects": "", "combinations": [["1"], ["2"]]},
        ],
    }
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/archi_profile",
        json={"title": "Профиль", "description": None, "diagnostic": config},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    # То, что реально уйдёт в форму правки — не собранное вручную, а то же,
    # чем сервер отвечает на настоящий экран.
    task = db.get(TrackerTask, task_id)
    stale_blocks = _edit_payloads(db, [task], {})[task_id]["blocks"]
    assert len(stale_blocks) == 1
    assert stale_blocks[0]["block_type"] == "question"
    assert all(not option["is_correct"] for option in stale_blocks[0]["options"])

    # Старый (баг) клиент — те же блоки уходят обратно при сохранении.
    broken = client.post(
        f"/cabinet/staff/program/items/{task_id}/archi_profile",
        json={"title": "Профиль", "description": None, "diagnostic": config, "blocks": stale_blocks},
        headers={"X-CSRF-Token": "x"},
    )
    assert broken.status_code == 422
    assert "верный ответ" in broken.text

    # Починенный клиент — для archi_profile blocks всегда пустой список.
    fixed = client.post(
        f"/cabinet/staff/program/items/{task_id}/archi_profile",
        json={"title": "Профиль", "description": None, "diagnostic": config, "blocks": []},
        headers={"X-CSRF-Token": "x"},
    )
    assert fixed.status_code == 200, fixed.text


def test_trainer_lets_staff_answer_the_legacy_diagnostic_without_saving_progress(
    client, db, user_factory, session_factory
):
    """Тренажёр (владелец 22.09.2026, вариант C развилки «доступ ГП/СА к
    диагностике» — см. `NEXT-CHAT-PROMPT-ДИАГНОСТИКА-ДОСТУП.md`): ГП и СА
    реально отвечают и видят результат, но ничего не пишется в
    `TaskBlockResponse` — реальный прогресс ученика не затрагивается, и
    повторный запуск не запирается «одной попыткой», в отличие от настоящего
    прохождения."""
    admin = user_factory(vk_id=887_301, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    today = today_msk()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Тренажёр", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    )
    assert cycle.status_code == 200
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/archi_profile",
        json={"title": "Диагностика АРХИ-ПРОФИЛЯ", "is_required": False, "blocks": []},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    superadmin = user_factory(vk_id=887_302, name="Суперадмин", is_admin=True, role_name="суперадмин")
    for actor in (admin, superadmin):
        client.cookies.set("session_id", session_factory(actor).id)
        responses_before = db.query(TaskBlockResponse).count()
        got = client.get(f"/cabinet/staff/program/tasks/{task_id}/trainer-blocks")
        assert got.status_code == 200, got.text
        blocks = got.json()["blocks"]
        assert len(blocks) == 3
        answers = [
            {"block_id": block["id"], "option_ids": [block["options"][digit - 1]["id"]]}
            for block, digit in zip(blocks, (1, 2, 1))
        ]
        # Один тренажёр можно пройти дважды подряд — не «одна попытка», как у
        # настоящего прохождения ученика, потому что результат нигде не хранится.
        for _ in range(2):
            scored = client.post(
                f"/cabinet/staff/program/tasks/{task_id}/trainer-score",
                json={"answers": answers}, headers={"X-CSRF-Token": "x"},
            )
            assert scored.status_code == 200, scored.text
            result = scored.json()["archi_profile"]
            assert result["combination"] == "121"
            assert result["title"] == "Архитектор-синтетик"
        assert db.query(TaskBlockResponse).count() == responses_before

    incomplete = client.post(
        f"/cabinet/staff/program/tasks/{task_id}/trainer-score",
        json={"answers": answers[:2]}, headers={"X-CSRF-Token": "x"},
    )
    assert incomplete.status_code == 422

    student = user_factory(vk_id=887_303, name="Ученик", role_name="ученик")
    client.cookies.set("session_id", session_factory(student).id)
    assert client.get(f"/cabinet/staff/program/tasks/{task_id}/trainer-blocks").status_code == 403
    assert client.post(
        f"/cabinet/staff/program/tasks/{task_id}/trainer-score",
        json={"answers": answers}, headers={"X-CSRF-Token": "x"},
    ).status_code == 403


def test_trainer_matches_teacher_authored_result_for_the_same_answers(
    client, db, user_factory, session_factory
):
    """Тренажёр считает результат той же логикой (`_profile_for_digits`), что
    и настоящее прохождение ученика — teacher-authored диагностика с
    `diagnostic_config`, а не только легаси-набор из трёх вопросов."""
    admin = user_factory(vk_id=887_401, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    today = today_msk()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Цикл тренажёра", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    )
    config = {
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
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/archi_profile",
        json={"title": "Профиль", "description": "Выбери ответы", "diagnostic": config},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    got = client.get(f"/cabinet/staff/program/tasks/{task_id}/trainer-blocks")
    assert got.status_code == 200, got.text
    blocks = got.json()["blocks"]
    answers = [
        {"block_id": blocks[0]["id"], "option_ids": [blocks[0]["options"][1]["id"]]},
        {"block_id": blocks[1]["id"], "option_ids": [blocks[1]["options"][0]["id"]]},
    ]
    scored = client.post(
        f"/cabinet/staff/program/tasks/{task_id}/trainer-score",
        json={"answers": answers}, headers={"X-CSRF-Token": "x"},
    )
    assert scored.status_code == 200, scored.text
    result = scored.json()["archi_profile"]
    assert result["title"] == "Создатель"
    assert result["combination"] == "2A"
    assert db.query(TaskBlockResponse).filter_by(task_id=task_id).count() == 0
