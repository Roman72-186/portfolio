"""Общая обратная связь на несколько заданий (созвон 13.10.2026, план
`plans/2026-10-14-apparchi-созвон-13-10.md`, часть 2).

Ученики сдают работы неделями без ответа; куратор отвечает один раз на все.
Связку ставит преподаватель на блоке сдачи «главного» задания. Правила —
`services/linked_feedback.py`, сторожа ниже держат их через настоящие роуты
и выборки."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.notification import Notification
from app.models.task_block import (
    BLOCK_TEXT,
    BLOCK_UPLOAD,
    TaskBlock,
    TaskBlockFeedbackLink,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
)
from app.models.tracker import TrackerTask
from app.services.linked_feedback import LinkError, sync_links
from app.services.review_aggregate import unreviewed_counts_by_student
from app.services.submission_edit import block_work_reason
from app.services.task_block_feedback import list_student_feedback_cards
from app.services.task_blocks import submission_review_queue, sync_blocks


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _task_with_block(db, title, block_type=BLOCK_UPLOAD):
    task = TrackerTask(title=title, kind="material", is_published=True, assign_to_all=True)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=block_type, title="Сдать листы")
    db.add(block)
    db.flush()
    return task, block


def _submit(db, block, student, when, photo=None):
    submission = TaskBlockSubmission(block_id=block.id, user_id=student.id, submitted_at=when)
    db.add(submission)
    db.flush()
    if photo:
        db.add(TaskBlockSubmissionImage(
            submission_id=submission.id, image_s3_url=photo, image_s3_path=None, sort_order=0,
        ))
    db.flush()
    return submission


@pytest.fixture
def group(db, user_factory):
    """Три задания с домашкой: два прошлых связаны с третьим. Ученик сдал
    оба прошлых; главное — по желанию теста."""
    curator = user_factory(vk_id=995_001, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=995_002, name="Ученик")
    student.curator_id = curator.id
    first_task, first = _task_with_block(db, "Неделя 1 – перспектива")
    second_task, second = _task_with_block(db, "Неделя 2 – светотень")
    main_task, main = _task_with_block(db, "Неделя 3 – натюрморт")
    sync_links(db, main, [first.id, second.id])
    start = datetime.now(timezone.utc) - timedelta(days=10)
    works = {
        "first": _submit(db, first, student, start, photo="https://s3/first.jpg"),
        "second": _submit(db, second, student, start + timedelta(days=3), photo="https://s3/second.jpg"),
    }
    db.commit()

    def submit_main():
        works["main"] = _submit(
            db, main, student, start + timedelta(days=9), photo="https://s3/main.jpg",
        )
        db.commit()
        return works["main"]

    return {
        "curator": curator, "student": student, "blocks": (first, second, main),
        "tasks": (first_task, second_task, main_task), "works": works, "submit_main": submit_main,
    }


def _staff_says(client, session_factory, curator, submission, text="Разбор всех работ"):
    _login(client, session_factory, curator)
    with patch("app.api.task_block_feedback.notify"):
        return client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": text},
        )


# --- связка в конструкторе ----------------------------------------------------

def test_links_are_saved_and_dropped_when_block_stops_taking_work(db, group):
    first, second, main = group["blocks"]
    stored = {(r.block_id, r.linked_block_id) for r in db.query(TaskBlockFeedbackLink)}
    assert stored == {(main.id, first.id), (main.id, second.id)}

    sync_blocks(db, task_id=main.task_id, items=[
        {"id": main.id, "block_type": BLOCK_TEXT, "title": "Теперь текст", "body": "Читай"},
    ])
    db.commit()

    assert db.query(TaskBlockFeedbackLink).count() == 0


def test_link_refuses_self_chain_and_second_main(db, group):
    first, second, main = group["blocks"]
    _other_task, other = _task_with_block(db, "Неделя 4")
    with pytest.raises(LinkError):
        sync_links(db, other, [other.id])
    # Блок уже входит в общую ОС задания 3.
    with pytest.raises(LinkError):
        sync_links(db, other, [first.id])
    # Цепочкой нельзя: задание 3 само собирает общую ОС.
    with pytest.raises(LinkError):
        sync_links(db, other, [main.id])
    # Связанный блок сам главным не становится.
    with pytest.raises(LinkError):
        sync_links(db, first, [other.id])
    _text_task, text_block = _task_with_block(db, "Теория", block_type=BLOCK_TEXT)
    with pytest.raises(LinkError):
        sync_links(db, other, [text_block.id])


def test_deleted_main_task_frees_its_linked_blocks(db, group):
    first, _second, _main = group["blocks"]
    group["tasks"][2].deleted_at = datetime.now(timezone.utc)
    db.commit()
    _other_task, other = _task_with_block(db, "Неделя 4")

    sync_links(db, other, [first.id])
    db.commit()

    assert db.query(TaskBlockFeedbackLink).filter_by(linked_block_id=first.id).one().block_id == other.id
    # И прошлая работа вернулась в очередь сама по себе.
    queue = submission_review_queue(db, student_id=group["student"].id)
    assert group["works"]["second"].id in {row["submission_id"] for row in queue}


# --- очередь куратора ---------------------------------------------------------

def test_linked_works_wait_silently_until_main_is_submitted(db, group):
    queue = submission_review_queue(db, student_id=group["student"].id)
    assert queue == []
    counts = unreviewed_counts_by_student(db, curator_id=None, student_id=group["student"].id)
    assert counts.get(group["student"].id, 0) == 0


def test_queue_shows_one_row_with_all_works(db, group):
    main = group["submit_main"]()

    queue = submission_review_queue(db, student_id=group["student"].id)

    assert [row["submission_id"] for row in queue] == [main.id]
    assert queue[0]["linked_count"] == 2
    assert queue[0]["images"] == [
        "https://s3/first.jpg", "https://s3/second.jpg", "https://s3/main.jpg",
    ]
    counts = unreviewed_counts_by_student(db, curator_id=None, student_id=group["student"].id)
    assert counts[group["student"].id] == 1


def test_work_answered_before_linking_keeps_its_own_dialog(db, group, client, session_factory):
    """Связка задним числом не отбирает у работы уже данный ей ответ."""
    second = group["works"]["second"]
    db.query(TaskBlockFeedbackLink).filter_by(linked_block_id=second.block_id).delete()
    db.commit()
    assert _staff_says(client, session_factory, group["curator"], second).status_code == 200
    sync_links(db, group["blocks"][2], [group["blocks"][0].id, group["blocks"][1].id])
    db.commit()
    main = group["submit_main"]()

    queue = submission_review_queue(db, student_id=group["student"].id)

    assert {row["submission_id"] for row in queue} == {main.id, second.id}
    assert next(row for row in queue if row["submission_id"] == main.id)["linked_count"] == 1


# --- экран сдачи ---------------------------------------------------------------

def test_main_screen_lists_linked_works_with_dates(db, group, client, session_factory):
    main = group["submit_main"]()
    _login(client, session_factory, group["curator"])

    page = client.get(f"/cabinet/staff/task-block-submissions/{main.id}/feedback")

    assert page.status_code == 200
    assert "Неделя 1 – перспектива" in page.text
    assert "Неделя 2 – светотень" in page.text
    assert "https://s3/first.jpg" in page.text


def test_linked_work_pages_lead_to_the_main_dialog(db, group, client, session_factory):
    main = group["submit_main"]()
    first = group["works"]["first"]

    _login(client, session_factory, group["curator"])
    staff = client.get(
        f"/cabinet/staff/task-block-submissions/{first.id}/feedback", follow_redirects=False,
    )
    assert staff.status_code == 302
    assert staff.headers["location"].endswith(f"/task-block-submissions/{main.id}/feedback")

    _login(client, session_factory, group["student"])
    own = client.get(f"/cabinet/task-block-submissions/{first.id}/feedback", follow_redirects=False)
    assert own.status_code == 302
    assert own.headers["location"] == f"/cabinet/task-block-submissions/{main.id}/feedback"


def test_staff_cannot_open_a_second_dialog_on_a_linked_work(db, group, client, session_factory):
    first = group["works"]["first"]

    response = _staff_says(client, session_factory, group["curator"], first)

    assert response.status_code == 409
    assert "общую обратную связь" in response.json()["error"]


def test_foreign_curator_cannot_open_the_group(db, group, client, session_factory, user_factory):
    main = group["submit_main"]()
    stranger = user_factory(vk_id=995_050, name="Чужой куратор", role_name="куратор")
    _login(client, session_factory, stranger)

    page = client.get(f"/cabinet/staff/task-block-submissions/{main.id}/feedback")

    assert page.status_code == 403


# --- ученик ----------------------------------------------------------------------

def test_answer_locks_linked_works_and_reaches_student_as_one_card(
    db, group, client, session_factory,
):
    main = group["submit_main"]()
    first_task, _second_task, _main_task = group["tasks"]
    first_block = group["blocks"][0]
    first = group["works"]["first"]
    assert block_work_reason(db, first_task, first_block, first) is None

    assert _staff_says(client, session_factory, group["curator"], main).status_code == 200

    assert "ответил" in block_work_reason(db, first_task, first_block, first)
    open_cards, _closed = list_student_feedback_cards(db, group["student"].id)
    assert len(open_cards) == 1
    assert open_cards[0]["group_label"].startswith("Общая обратная связь: работы с ")
    assert open_cards[0]["group_label"].endswith("(3)")
    note = db.query(Notification).filter_by(user_id=group["student"].id).one()
    assert note.title == "Преподаватель оставил обратную связь по работам"
    assert note.task_block_submission_id == main.id


# --- поиск в конструкторе --------------------------------------------------------

def test_source_search_offers_only_submission_blocks(db, user_factory, session_factory, client):
    head = user_factory(vk_id=995_100, name="Главный", is_admin=True, role_name="админ")
    _work_task, work_block = _task_with_block(db, "Сдача эскиза")
    _text_task, _text = _task_with_block(db, "Сдача теории", block_type=BLOCK_TEXT)
    db.commit()
    _login(client, session_factory, head)

    found = client.get("/cabinet/staff/program/blocks-source?submission_only=1&q=Сдача").json()["items"]

    assert [item["title"] for item in found] == ["Сдача эскиза"]
    assert [block["id"] for block in found[0]["blocks"]] == [work_block.id]
    plain = client.get("/cabinet/staff/program/blocks-source?q=Сдача").json()["items"]
    assert {item["title"] for item in plain} == {"Сдача эскиза", "Сдача теории"}
    assert "blocks" not in plain[0]


def test_constructor_route_refuses_bad_link_with_reason(db, user_factory, session_factory, client):
    """Отказ связки доходит до преподавателя текстом (422), а не 500."""
    from app.services.tz import today_msk

    head = user_factory(vk_id=995_200, name="Главный", is_admin=True, role_name="админ")
    _text_task, text_block = _task_with_block(db, "Теория", block_type=BLOCK_TEXT)
    db.commit()
    _login(client, session_factory, head)
    today = today_msk()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Цикл", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    ).json()["cycle_id"]

    response = client.post(
        f"/cabinet/staff/program/cycles/{cycle}/items/material",
        json={"title": "Неделя 3", "is_required": True, "blocks": [
            {"block_type": BLOCK_UPLOAD, "title": "Сдать", "linked_block_ids": [text_block.id]},
        ]},
        headers={"X-CSRF-Token": "x"},
    )

    assert response.status_code == 422
    assert "со сдачей работы" in response.json()["detail"]
    assert db.query(TaskBlockFeedbackLink).count() == 0


def test_edit_form_sent_back_whole_keeps_links(db, user_factory, session_factory, client):
    """Форма правки (`_edit_payloads`), отправленная обратно целиком,
    принимается и связку сохраняет: подписи `linked_blocks` сервер берёт и
    не читает, связь — из `linked_block_ids` (до 14.10.2026 вечером было 422)."""
    from app.api.cabinet_program import _edit_payloads
    from app.services.tz import today_msk

    head = user_factory(vk_id=995_250, name="Главный", is_admin=True, role_name="админ")
    _first_task, first = _task_with_block(db, "Неделя 1 – перспектива")
    _second_task, second = _task_with_block(db, "Неделя 2 – светотень")
    db.commit()
    _login(client, session_factory, head)
    today = today_msk()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Цикл", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    ).json()["cycle_id"]
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle}/items/material",
        json={"title": "Неделя 3", "is_required": True, "blocks": [
            {"block_type": BLOCK_UPLOAD, "title": "Сдать",
             "linked_block_ids": [first.id, second.id]},
        ]},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    payload = _edit_payloads(db, [db.get(TrackerTask, task_id)], {})[task_id]
    block = payload["blocks"][0]
    assert sorted(block["linked_block_ids"]) == sorted([first.id, second.id])
    assert {choice["id"] for choice in block["linked_blocks"]} == {first.id, second.id}
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
    main = db.query(TaskBlock).filter_by(task_id=task_id).one()
    assert {
        link.linked_block_id
        for link in db.query(TaskBlockFeedbackLink).filter_by(block_id=main.id)
    } == {first.id, second.id}


def test_source_search_hides_blocks_the_link_would_refuse(db, group, user_factory, session_factory, client):
    """Поиск не предлагает того, что `sync_links` отвергнет: главный блок
    чужой общей ОС и блоки, уже связанные с другим заданием. Свои связи
    главного блока при этом остаются в выдаче."""
    first, second, main = group["blocks"]
    head = user_factory(vk_id=995_300, name="Главный", is_admin=True, role_name="админ")
    _login(client, session_factory, head)

    def offered(main_block_id=None):
        url = "/cabinet/staff/program/blocks-source?submission_only=1&q=Неделя"
        if main_block_id:
            url += f"&main_block_id={main_block_id}"
        return {b["id"] for item in client.get(url).json()["items"] for b in item["blocks"]}

    assert offered() == set()
    assert offered(main.id) == {first.id, second.id}
