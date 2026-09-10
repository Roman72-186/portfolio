"""Задания внутри цикла (`app/api/cabinet_program.py`, экран
`cabinet_program_cycle_items.html`) — замена календарного дня как способа
заводить задания (владелец 10.09.2026).

Скрипт страницы держат те же два сторожа, что у `cabinet_program_day.html`
(`tests/test_program_day_script.py`) — сюда переехал общий блочный
конструктор через `{% include %}`, и здесь та же пара аварий возможна:
удалили вызов без объявления, или Jinja-цикл сломал JS-литерал.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta

from app.models.learning_topic import LearningTopic
from app.models.tracker import TrackerTask
from app.services.tz import today_msk

# Тот же набор, что у test_program_day_script.py — платформенные глобальные,
# на которые сторож не должен ругаться.
KNOWN_GLOBALS = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "new", "in", "of", "do", "else", "try", "throw", "delete", "void",
    "Array", "Object", "JSON", "String", "Number", "Boolean", "Promise",
    "Date", "Math", "Set", "Map", "RegExp", "Error", "FormData", "URL",
    "URLSearchParams", "Blob", "File", "FileReader", "IntersectionObserver",
    "MutationObserver", "CustomEvent", "Event",
    "fetch", "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
    "decodeURIComponent", "setTimeout", "clearTimeout", "setInterval",
    "clearInterval", "requestAnimationFrame", "alert", "confirm", "prompt",
    "console", "queueMicrotask", "structuredClone", "AbortController",
}


def _strip_noise(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    js = re.sub(r"(?m)^\s*//.*$", " ", js)
    js = re.sub(r"//[^\n'\"]*$", " ", js, flags=re.M)
    js = re.sub(r"'(?:\\.|[^'\\])*'", "''", js)
    js = re.sub(r'"(?:\\.|[^"\\])*"', '""', js)
    return js


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


def test_cycle_items_page_script_calls_only_defined_functions(
    client, db, user_factory, session_factory
):
    _csrf_client(client, user_factory, session_factory)
    cycle_id = _make_cycle(client)

    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert page.status_code == 200

    for raw in re.findall(r"<script>(.*?)</script>", page.text, re.S):
        js = _strip_noise(raw)
        declared = set(re.findall(r"function\s+(\w+)", js))
        declared |= set(re.findall(r"\bvar\s+(\w+)", js))
        declared |= set(re.findall(r"\b(\w+)\s*=\s*function", js))
        for params in re.findall(r"function[^(]*\(([^)]*)\)", js):
            declared |= {p.strip() for p in params.split(",") if p.strip()}

        called = set(re.findall(r"(?<![.\w$])([A-Za-z_$]\w*)\s*\(", js))
        missing = sorted(called - declared - KNOWN_GLOBALS)

        assert not missing, f"вызовы без определения: {missing}"


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

    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert "Ограничить по тарифу" not in page.text

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
