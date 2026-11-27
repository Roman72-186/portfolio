"""Задания внутри цикла и скрипт редактора."""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta

import pytest

from app.models.learning_topic import LearningTopic
from app.models.tracker import TrackerTask
from app.services.task_blocks import block_opens_at, block_still_doable, get_blocks, submit_deadline_for
from app.services.tz import parse_msk_local, today_msk

def _csrf_client(client, user_factory, session_factory, *, vk_id=700_501):
    admin = user_factory(vk_id=vk_id, name="Главный", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    return admin


def _make_cycle(client, *, title="Цикл"):
    today = today_msk()
    resp = client.post(
        "/cabinet/staff/program/cycles",
        json={
            "title": title,
            "description": None,
            "starts_on": today.isoformat(),
            "ends_on": (today + timedelta(days=5)).isoformat(),
            "is_published": True,
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["cycle_id"]


@pytest.mark.parametrize("block_type", ["photo", "video", "link", "photo_upload"])
def test_cycle_item_rejects_incomplete_block(client, db, user_factory, session_factory, block_type):
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)
    headers = {"X-CSRF-Token": "x"}
    title = "Эскизы"
    blocks = [{"block_type": block_type}]

    rejected = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": title, "blocks": blocks}, headers=headers,
    )
    assert rejected.status_code == 422
    assert "1" in rejected.json()["detail"]
    assert not db.query(TrackerTask).filter_by(topic_id=cycle_id).count()


def test_cycle_item_keeps_saved_submission_when_condition_is_cleared(
    client, db, user_factory, session_factory,
):
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)
    headers = {"X-CSRF-Token": "x"}
    block = {"block_type": "photo_upload", "body": "Пришлите два эскиза", "required_photos": 2}
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": "Эскизы", "blocks": [block]}, headers=headers,
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    saved = get_blocks(db, task_id)[0]
    rejected = client.post(
        f"/cabinet/staff/program/items/{task_id}/material",
        json={"title": "Новое название", "blocks": [{**block, "id": saved.id, "body": ""}]},
        headers=headers,
    )
    assert rejected.status_code == 422
    db.expire_all()
    assert get_blocks(db, task_id)[0].body == block["body"]
    assert db.get(TrackerTask, task_id).title == "Эскизы"


def test_nested_containers_save_settings_and_move_with_children(client, db, user_factory, session_factory):
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)
    headers = {"X-CSRF-Token": "x"}
    outer = {"key": "outer", "title": "Основной блок", "opens_at": "2026-11-25T12:00",
             "closes_at": "2026-12-02T12:00", "submit_until": "2026-12-01T12:00"}
    middle = {"key": "middle", "title": "Вложенный блок", "submit_until": "2026-11-30T12:00"}
    inner = {"key": "inner", "title": "Внутренний блок"}
    sibling = {"key": "sibling", "title": "Дополнительный блок"}
    blocks = [
        {"block_type": "text", "body": "Материалы", "container_path": [outer, middle, inner]},
        {"block_type": "text", "body": "Другие материалы", "container_path": [outer, sibling]},
    ]
    response = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": "Контрольная", "blocks": blocks}, headers=headers,
    )
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    first, second = get_blocks(db, task_id)
    first_id, second_id = first.id, second.id
    assert [node["title"] for node in first.container_path] == [outer["title"], middle["title"], inner["title"]]
    assert block_opens_at(first) == parse_msk_local(outer["opens_at"])
    assert not block_still_doable(first, None, now=parse_msk_local("2026-12-03T12:00"))
    assert submit_deadline_for(first, None, user_tariff=None) == parse_msk_local(middle["submit_until"])

    updated = client.post(
        f"/cabinet/staff/program/items/{task_id}/material",
        json={"title": "Контрольная", "blocks": [
            {**blocks[1], "id": second_id},
            {**blocks[0], "id": first_id, "container_path": [middle, inner]},
        ]}, headers=headers,
    )
    assert updated.status_code == 200, updated.text
    db.expire_all()
    first, second = get_blocks(db, task_id)
    assert (first.id, second.id) == (second_id, first_id)
    assert [node["key"] for node in second.container_path] == ["middle", "inner"]
    assert block_opens_at(second) is None


def test_cycle_rejects_a_container_inside_itself(client, db, user_factory, session_factory):
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)
    response = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": "Контрольная", "blocks": [
            {"block_type": "text", "body": "Материалы", "container_path": [{"key": "a"}, {"key": "a"}]},
        ]}, headers={"X-CSRF-Token": "x"},
    )
    assert response.status_code == 422
    assert "самого себя" in response.json()["detail"]


def test_cycle_items_page_scripts_are_valid_javascript(
    client, db, user_factory, session_factory
):
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("Node.js не установлен — синтаксис скрипта не проверить")

    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)

    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert page.status_code == 200

    scripts = re.findall(r"<script>(.*?)</script>", page.text, re.S)
    assert scripts, "на странице заданий цикла не нашлось ни одного скрипта"

    with tempfile.TemporaryDirectory() as tmp:
        for number, raw in enumerate(scripts):
            path = pathlib.Path(tmp) / f"cycle_items_script_{number}.js"
            path.write_text(raw, encoding="utf-8")
            check = subprocess.run(
                [node, "--check", str(path)], capture_output=True, text=True
            )
            assert check.returncode == 0, (
                f"скрипт #{number} страницы заданий цикла не разбирается: " + check.stderr
            )


def test_create_edit_move_delete_cycle_item(client, db, user_factory, session_factory):
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)

    create = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Первое задание", "description": "описание", "subject": None,
            "is_required": True, "starts_on": None,
            "blocks": [{"block_type": "text", "body": "привет"}],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert create.status_code == 200, create.text
    task_id = create.json()["task_id"]

    task = db.get(TrackerTask, task_id)
    assert task.due_at is None
    assert task.topic_id == cycle_id
    assert task.sort_order == 0

    second = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Второе задание", "description": None, "subject": None,
            "is_required": False, "starts_on": None, "blocks": [],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert second.status_code == 200, second.text
    task_id2 = second.json()["task_id"]
    db.expire_all()
    assert db.get(TrackerTask, task_id2).sort_order == 1

    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert "Первое задание" in page.text
    assert "Второе задание" in page.text

    # Правка через существующий роут /items/{id}/material — не переименовывает цикл.
    edit = client.post(
        f"/cabinet/staff/program/items/{task_id}/material",
        json={
            "title": "Первое задание (правка)", "description": None, "subject": None,
            "is_required": True, "starts_on": None, "blocks": [],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert edit.status_code == 200, edit.text
    db.expire_all()
    topic = db.get(LearningTopic, cycle_id)
    assert topic.title == "Цикл"

    # Не роняет 500 на due_at=None (регрессия найденного бага).
    assert edit.status_code != 500

    # Перестановка: второе задание поднимается выше первого.
    move = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/{task_id2}/move",
        json={"direction": -1},
        headers={"X-CSRF-Token": "x"},
    )
    assert move.status_code == 200, move.text
    db.expire_all()
    assert db.get(TrackerTask, task_id2).sort_order < db.get(TrackerTask, task_id).sort_order

    # Несуществующее задание — 404, а не 500.
    move_missing = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/999999/move",
        json={"direction": -1},
        headers={"X-CSRF-Token": "x"},
    )
    assert move_missing.status_code == 404

    delete = client.post(
        f"/cabinet/staff/program/items/{task_id2}/delete",
        json={},
        headers={"X-CSRF-Token": "x"},
    )
    assert delete.status_code == 200, delete.text
    page_after = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert "Второе задание" not in page_after.text


def test_cycle_item_form_has_no_tariff_field(client, db, user_factory, session_factory):
    """Адресация внутри цикла — по блокам, не по рамке (владелец 06.09.2026):
    форма создания задания внутри цикла не должна отдавать даже возможность
    ограничить по тарифу — она перезаписала бы тариф всего цикла."""
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)

    assert client.get(f"/cabinet/staff/program/cycles/{cycle_id}").status_code == 200

    # Сервер тоже отказывает, если кто-то пришлёт лишнее поле напрямую.
    resp = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={
            "title": "Задание", "description": None, "subject": None,
            "is_required": True, "starts_on": None, "blocks": [],
            "audience": {"tariff_restricted": True, "tariffs": ["МАКСИМУМ"]},
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert resp.status_code == 422


def test_cycle_without_title_shows_period_as_label(client, db, user_factory, session_factory):
    """Название цикла необязательно (владелец 10.09.2026) — список и экран
    заданий показывают период вместо пустой строки."""
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client, title="")

    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert page.status_code == 200
    today = today_msk()
    expected_start = today.strftime("%d.%m")
    assert expected_start in page.text

    cycles_list = client.get("/cabinet/staff/program/cycles")
    assert expected_start in cycles_list.text


def test_whole_cycle_preview_filters_by_level_and_student_like_the_feed(
    client, db, user_factory, session_factory
):
    """«Глазами ученика» учитывает уровень точки А и учеников поимённо, а не
    только тариф (владелец 08.10.2026). Правило отбора на странице — перенос
    `task_blocks.is_block_open_to`: прогоняем его в Node.js на тех же
    случаях, что серверное правило, и сверяем ответы. Ничего не выбрано —
    обзор, видно всё, как и раньше."""
    import json

    import pytest

    from app.services.task_blocks import BlockAudience, BlockViewer, is_block_open_to

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js не установлен — правило просмотра не прогнать")

    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": "Введение", "is_required": True, "blocks": [
            {"block_type": "text", "title": "Теория", "body": "Текст"},
        ]},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}").text
    assert "data-cycle-preview-level" in page
    assert "data-cycle-preview-student" in page

    start = page.index("function previewBlockOpenTo(")
    end = page.index("\n    }\n", start) + len("\n    }\n")
    function = page[start:end]

    audiences = [
        {},
        {"tariffs": ["А"]},
        {"levels": [2]},
        {"tariffs": ["А"], "levels": [2]},
        {"student_ids": [7]},
        {"tariffs": ["Б"], "student_ids": [7]},
        {"levels": [1], "student_ids": [7]},
    ]
    viewers = [
        {"tariff": "А", "level": 2, "studentId": 0},
        {"tariff": "А", "level": 1, "studentId": 0},
        {"tariff": "Б", "level": 2, "studentId": 0},
        {"tariff": "Б", "level": 1, "studentId": 7},
        {"tariff": "А", "level": 2, "studentId": 9},
    ]
    cases = [(a, v) for a in audiences for v in viewers]

    expected = [
        is_block_open_to(
            BlockAudience(
                tariffs=frozenset(a.get("tariffs", [])),
                levels=frozenset(a.get("levels", [])),
                user_ids=frozenset(a.get("student_ids", [])),
            ),
            BlockViewer(None, user_id=v["studentId"] or None, tariff=v["tariff"], level=v["level"]),
        )
        for a, v in cases
    ]
    script = function + (
        "\nvar cases = " + json.dumps(cases, ensure_ascii=False) + ";"
        "\nconsole.log(JSON.stringify(cases.map(function (c) {"
        " return previewBlockOpenTo(c[0], c[1]); })));"
        "\nconsole.log(JSON.stringify(previewBlockOpenTo({student_ids: [7], levels: [2]},"
        " {tariff: '', level: 0, studentId: 0})));"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "preview_rule.js"
        path.write_text(script, encoding="utf-8")
        run = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    lines = run.stdout.strip().splitlines()
    assert json.loads(lines[0]) == expected
    assert json.loads(lines[1]) is True  # обзор: ничего не выбрано — видно всё


# ── «Глазами ученика» с плеером и для куратора (созвон 13.10.2026) ────────────


def test_curator_with_view_sees_cycle_and_named_students_without_nick(
    client, db, user_factory, session_factory
):
    """Куратору открыт АОП на «Смотреть» (созвон 13.10.2026): экран цикла и
    «Весь цикл глазами ученика» есть, правки нет. В выборе ученика —
    только отмеченные поимённо в блоках цикла, имя без ника; остальных
    учеников школы куратору этот экран не отдаёт."""
    from app.models.section_access import SectionAccessRule

    named = user_factory(vk_id=700_610, name="Названная")
    named.last_name, named.first_name, named.tg_username = "Иванова", "Маша", "masha_nick"
    other = user_factory(vk_id=700_611, name="Другая")
    other.last_name, other.first_name = "Петрова", "Оля"
    db.commit()

    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": "Введение", "is_required": True, "blocks": [
            {"block_type": "text", "title": "Теория", "body": "Текст",
             "student_ids": [named.id]},
        ]},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text

    curator = user_factory(vk_id=700_612, name="Куратор", role_name="куратор")
    db.add(SectionAccessRule(section_key="program", user_id=curator.id, level="view"))
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)

    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}", follow_redirects=False)
    assert page.status_code == 200
    assert "Весь цикл глазами ученика" in page.text
    raw = re.search(r"var BLOCK_STUDENTS = (.*?);\n", page.text).group(1)
    students = json.loads(raw)
    assert [(row["id"], row["name"], row["username"]) for row in students] == [
        (named.id, "Иванова Маша", ""),
    ]
    assert "masha_nick" not in page.text

    write = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": "Ещё", "is_required": True, "blocks": []},
        headers={"X-CSRF-Token": "x", "Accept": "application/json"},
    )
    assert write.status_code == 403


def test_create_request_retry_returns_original_task(admin_client):
    client, _ = admin_client
    cycle_id = _make_cycle(client)
    path = f"/cabinet/staff/program/cycles/{cycle_id}/items/material"
    payload = {"title": "Задание", "creation_request_id": "a84c5e10-6e98-41f4-a593-ce33dec9b2e2"}
    first = client.post(path, json=payload)
    second = client.post(path, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_create_request_retry_rejects_edited_payload(admin_client, db):
    client, _ = admin_client
    cycle_id = _make_cycle(client)
    path = f"/cabinet/staff/program/cycles/{cycle_id}/items/material"
    payload = {"title": "Исходное", "creation_request_id": "86d51b94-c520-4f93-b090-6feb755c9a11"}
    first = client.post(path, json=payload)
    assert first.status_code == 200

    changed = client.post(path, json={**payload, "title": "После обрыва связи"})

    assert changed.status_code == 409
    task = db.query(TrackerTask).filter_by(topic_id=cycle_id, creation_request_id=payload["creation_request_id"]).one()
    assert task.title == "Исходное"
    assert client.post(path, json=payload).json() == first.json()


def test_stale_revision_cannot_overwrite_new_edit(admin_client, db):
    from app.api.cabinet_program import _item_revision

    client, _ = admin_client
    cycle_id = _make_cycle(client)
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material", json={"title": "Исходное"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    stale = _item_revision(db.get(TrackerTask, task_id))
    path = f"/cabinet/staff/program/items/{task_id}/material"
    assert client.post(path, json={"title": "Свежее", "revision": stale}).status_code == 200
    result = client.post(path, json={"title": "Устаревшее", "revision": stale})
    assert result.status_code == 409
    db.expire_all()
    assert db.get(TrackerTask, task_id).title == "Свежее"


def test_tariff_deadline_edit_invalidates_open_assignment_form(admin_client, db):
    from app.api.cabinet_program import _item_revision

    client, _ = admin_client
    cycle_id = _make_cycle(client)
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material", json={"title": "Задание"},
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    stale = _item_revision(db.get(TrackerTask, task_id))
    path = f"/cabinet/staff/program/items/{task_id}"
    override = {"tariff": "Я С ВАМИ", "submit_until": "2026-12-15T19:00"}
    assert client.post(f"{path}/deadline", json={"submit_deadlines": [override]}).status_code == 200

    assert client.post(f"{path}/material", json={"title": "Задание", "revision": stale}).status_code == 409
