"""Статистика прохождения диагностики: не начал / начал / закончил, время,
распределение профилей (владелец 24.09.2026)."""

from datetime import timedelta

from app.services.activity_stats import get_diagnostic_stats
from app.services.archi_profile_stats import (
    STATUS_FINISHED,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    diagnostic_stats,
)
from app.services.tz import today_msk
from app.models.tracker import TrackerTask


def _create_cycle_with_diagnostic(client, today):
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
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/archi_profile",
        json={"title": "Диагностика АРХИ-ПРОФИЛЯ", "is_required": False, "blocks": []},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    return created.json()["task_id"]


def test_not_started_in_progress_and_finished_are_told_apart(
    client, db, user_factory, session_factory
):
    admin = user_factory(vk_id=888_001, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    task_id = _create_cycle_with_diagnostic(client, today_msk())

    not_started = user_factory(vk_id=888_002, name="Аня", role_name="ученик")
    in_progress = user_factory(vk_id=888_003, name="Боря", role_name="ученик")
    finished = user_factory(vk_id=888_004, name="Вика", role_name="ученик")

    endpoint = f"/cabinet/tracker/tasks/{task_id}/blocks"

    # `not_started` не трогает эндпоинт вовсе — остаётся «не начал».
    # `in_progress` открывает диагностику (это уже ставит started_at) и
    # отвечает на один вопрос из трёх — статус «начал, не закончил».
    client.cookies.set("session_id", session_factory(in_progress).id)
    partial = client.get(endpoint)
    assert partial.status_code == 200
    blocks = partial.json()["blocks"]
    client.post(endpoint, json={"answers": [
        {"block_id": blocks[0]["id"], "option_ids": [blocks[0]["options"][0]["id"]]},
    ]}, headers={"X-CSRF-Token": "x"})

    client.cookies.set("session_id", session_factory(finished).id)
    blocks = client.get(endpoint).json()["blocks"]
    answers = [
        {"block_id": block["id"], "option_ids": [block["options"][digit - 1]["id"]]}
        for block, digit in zip(blocks, (1, 2, 1))
    ]
    saved = client.post(endpoint, json={"answers": answers}, headers={"X-CSRF-Token": "x"})
    assert saved.status_code == 200, saved.text

    task = db.get(TrackerTask, task_id)
    stats = diagnostic_stats(db, task)
    by_id = {row["student"].id: row for row in stats["rows"]}

    assert by_id[not_started.id]["status"] == STATUS_NOT_STARTED
    assert by_id[not_started.id]["started_at_text"] == ""

    assert by_id[in_progress.id]["status"] == STATUS_IN_PROGRESS
    assert by_id[in_progress.id]["started_at_text"] != ""
    assert by_id[in_progress.id]["finished_at_text"] == ""

    assert by_id[finished.id]["status"] == STATUS_FINISHED
    assert by_id[finished.id]["profile"]["title"] == "Архитектор-синтетик"
    assert by_id[finished.id]["started_at_text"] != ""
    assert by_id[finished.id]["finished_at_text"] != ""
    assert by_id[finished.id]["duration_text"] is not None

    assert stats["status_counts"] == {
        STATUS_NOT_STARTED: 1, STATUS_IN_PROGRESS: 1, STATUS_FINISHED: 1,
    }
    assert stats["profile_counts"] == [("Архитектор-синтетик", 1)]
    assert stats["total"] == 3


def test_diagnostic_shows_up_on_the_activity_stats_page(
    client, db, user_factory, session_factory
):
    """Статистика диагностики переехала в общую «Статистику активности»
    (владелец 24.09.2026) — своей отдельной страницы у неё больше нет."""
    admin = user_factory(vk_id=888_101, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    task_id = _create_cycle_with_diagnostic(client, today_msk())
    student = user_factory(vk_id=888_102, name="Ученик", role_name="ученик")

    activity_page = client.get("/cabinet/superadmin/activity")
    assert activity_page.status_code == 200
    assert "Диагностика АРХИ-ПРОФИЛЯ" in activity_page.text
    assert "Не начал" in activity_page.text
    assert "Ученик" in activity_page.text

    # Старый отдельный роут снят целиком.
    assert client.get(f"/cabinet/staff/program/tasks/{task_id}/diagnostic-stats").status_code == 404

    client.cookies.set("session_id", session_factory(student).id)
    assert client.get("/cabinet/superadmin/activity").status_code == 403


def test_audience_matches_cycle_topic_not_task_flags(client, db, user_factory, session_factory):
    """Диагностика внутри цикла не имеет собственной адресации — аудиторию
    задаёт тема цикла (`assign_to_all=True` у `create_topic` для циклов), а
    не пустые `TrackerTask.assign_to_all`/теги самой задачи."""
    admin = user_factory(vk_id=888_201, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    task_id = _create_cycle_with_diagnostic(client, today_msk())
    task = db.get(TrackerTask, task_id)
    assert task.assign_to_all is False

    student = user_factory(vk_id=888_202, name="Ученик", role_name="ученик")
    stats = diagnostic_stats(db, task)
    assert student.id in {row["student"].id for row in stats["rows"]}


def test_activity_aggregator_lists_both_kinds_and_skips_unpublished(
    client, db, user_factory, session_factory
):
    """`get_diagnostic_stats` (activity_stats.py) собирает и отдельный вид
    archi_profile, и диагностику, встроенную в обычное «Задание» — но не
    черновики и не удалённые."""
    admin = user_factory(vk_id=888_301, name="Преподаватель", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    today = today_msk()

    standalone_id = _create_cycle_with_diagnostic(client, today)

    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Второй цикл", "description": None,
              "starts_on": today.isoformat(), "ends_on": (today + timedelta(days=5)).isoformat(),
              "is_published": True},
        headers={"X-CSRF-Token": "x"},
    )
    config = {
        "questions": [{"text": "Вопрос", "options": [
            {"text": "Свет", "value": "1"}, {"text": "Форма", "value": "2"},
        ]}],
        "results": [{"title": "Итог", "text": "Формула", "architects": "",
                     "combinations": [["1"], ["2"]]}],
    }
    embedded = client.post(
        f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/material",
        json={"title": "Материал со встроенной диагностикой", "description": None,
              "subject": None, "is_required": True, "starts_on": None,
              "blocks": [{"block_type": "diagnostic", "diagnostic": config}]},
        headers={"X-CSRF-Token": "x"},
    )
    assert embedded.status_code == 200, embedded.text
    embedded_id = embedded.json()["task_id"]

    # Черновик — не должен попасть в список.
    draft_task2 = client.post(
        f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/archi_profile",
        json={"title": "Черновик диагностики", "is_required": False, "blocks": []},
        headers={"X-CSRF-Token": "x"},
    )
    draft2_id = draft_task2.json()["task_id"]
    draft2 = db.get(TrackerTask, draft2_id)
    draft2.is_published = False
    db.commit()

    titles = {d["task"].title for d in get_diagnostic_stats(db)}
    assert "Диагностика АРХИ-ПРОФИЛЯ" in titles
    assert "Материал со встроенной диагностикой" in titles
    assert "Черновик диагностики" not in titles
