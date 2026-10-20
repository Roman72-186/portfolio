"""Задание со сданными работами не удаляется, а добавление и удаление
заданий пишутся в журнал с автором (владелец 20.10.2026).

Прецедент 14.10.2026: задание №111 с блоком «Скопируй рисунок на глаз»
удалили из конструктора как дубль, 83 сданные работы на шесть дней пропали у
учеников и кураторов, а в журнале удаления не было — автора нашли по адресу в
логе запросов. Правило — `services/tracker.py::delete_task`."""

import json
from datetime import datetime, timedelta, timezone

from app.models.audit_log import AuditLog
from app.models.task_block import BLOCK_PHOTO_UPLOAD, TaskBlock, TaskBlockSubmission
from app.models.tracker import TrackerTask
from app.services.activity_stats import audit_label, get_audit_feed
from app.services.tz import today_msk

CSRF = {"X-CSRF-Token": "x"}


def _staff(client, user_factory, session_factory):
    admin = user_factory(vk_id=720_001, name="Лиза", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    return admin


def _cycle_with_task(client, title="1 неделя l математический метод l композиция"):
    today = today_msk()
    cycle = client.post("/cabinet/staff/program/cycles", json={
        "title": "1 неделя", "description": None, "starts_on": today.isoformat(),
        "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True,
    }, headers=CSRF)
    assert cycle.status_code == 200, cycle.text
    created = client.post(f"/cabinet/staff/program/cycles/{cycle.json()['cycle_id']}/items/material", json={
        "title": title, "description": None, "subject": "Рисунок", "is_required": True,
        "starts_on": None, "blocks": [{"block_type": "text", "body": "Скопируй рисунок на глаз"}],
    }, headers=CSRF)
    assert created.status_code == 200, created.text
    return created.json()["task_id"]


def _submit_work(db, task_id, student, *, submitted=True):
    block = TaskBlock(task_id=task_id, block_type=BLOCK_PHOTO_UPLOAD, title="Скопируй рисунок на глаз")
    db.add(block)
    db.flush()
    db.add(TaskBlockSubmission(
        block_id=block.id, user_id=student.id,
        submitted_at=datetime.now(timezone.utc) if submitted else None,
    ))
    db.commit()


def _actions(db, action):
    return [r for r in db.query(AuditLog).filter(AuditLog.action == action)]


def test_task_with_submitted_works_is_not_deleted(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    task_id = _cycle_with_task(client)
    _submit_work(db, task_id, user_factory(vk_id=720_002, name="Ученик"))

    refused = client.post(f"/cabinet/staff/program/items/{task_id}/delete", json={}, headers=CSRF)

    assert refused.status_code == 409
    assert "сданные работы" in refused.json()["detail"]
    assert "(1)" in refused.json()["detail"]
    db.expire_all()
    task = db.get(TrackerTask, task_id)
    assert task.deleted_at is None
    assert _actions(db, "program_item_delete") == []


def test_draft_without_submission_does_not_block_delete(client, db, user_factory, session_factory):
    """Черновик (фото загружено, «Сдать» не нажато) — ещё не сданная работа."""
    staff = _staff(client, user_factory, session_factory)
    task_id = _cycle_with_task(client)
    _submit_work(db, task_id, user_factory(vk_id=720_003, name="Ученик"), submitted=False)

    removed = client.post(f"/cabinet/staff/program/items/{task_id}/delete", json={}, headers=CSRF)

    assert removed.status_code == 200, removed.text
    db.expire_all()
    assert db.get(TrackerTask, task_id).deleted_at is not None
    [row] = _actions(db, "program_item_delete")
    assert row.performed_by_id == staff.id
    details = json.loads(row.details)
    assert details["task_id"] == task_id
    assert details["title"] == "1 неделя l математический метод l композиция"


def test_tracker_screen_refuses_the_same_way(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    task_id = _cycle_with_task(client)
    _submit_work(db, task_id, user_factory(vk_id=720_004, name="Ученик"))

    refused = client.post(f"/cabinet/staff/tracker/tasks/{task_id}/delete", headers=CSRF)

    assert refused.status_code == 409
    assert "сданные работы" in refused.json()["detail"]
    db.expire_all()
    assert db.get(TrackerTask, task_id).deleted_at is None


def test_create_is_logged_with_author_task_and_title(client, db, user_factory, session_factory):
    staff = _staff(client, user_factory, session_factory)
    task_id = _cycle_with_task(client, title="2 неделя l Адаптация l рисунок")

    [row] = _actions(db, "program_cycle_item_create")
    assert row.performed_by_id == staff.id
    details = json.loads(row.details)
    assert details["task_id"] == task_id
    assert details["title"] == "2 неделя l Адаптация l рисунок"


def test_journal_reads_task_actions_in_russian(client, db, user_factory, session_factory):
    assert audit_label("program_material_create") == "Задание: добавлено"
    assert audit_label("program_cycle_item_create") == "Задание: добавлено"
    assert audit_label("program_homework_update") == "Задание: изменено"
    assert audit_label("program_item_delete") == "Задание: удалено"
    assert audit_label("tracker_task_delete") == "Задание: удалено"
    assert audit_label("program_material_restore") == "Задание: восстановлено"
    # Рамки программы — своими словами, не «Задание».
    assert audit_label("program_stage_delete") == "Этап: удалён"
    assert audit_label("program_cycle_delete") == "Цикл: удалён"
    assert audit_label("tariff_change") == "Смена тарифа"

    _staff(client, user_factory, session_factory)
    _cycle_with_task(client)
    labels = {row["action_label"] for row in get_audit_feed(db)}
    assert "Задание: добавлено" in labels
