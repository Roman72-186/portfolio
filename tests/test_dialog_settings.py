"""«Настройка диалога» у блока сдачи — кто из учеников может ответить на
обратную связь куратора и сколькими сообщениями (созвон 30.09.2026, правила
подтверждены владельцем 01.10.2026, план
`plans/2026-10-01-apparchi-call-30-09-followup.md`, вопросы О1–О9, О22).

Правило живёт в одном месте — `task_block_feedback.student_can_reply`;
сторожа ниже держат каждое его условие через настоящий роут."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.api.cabinet_program import BlockItem
from app.constants import TARIFF_CONFIDENT_MAX, TARIFF_SELF, TARIFF_WITH_YOU
from app.models.task_block import (
    BLOCK_PHOTO_UPLOAD,
    BLOCK_TIMED,
    BLOCK_UPLOAD,
    TaskBlock,
    TaskBlockDialogTariff,
    TaskBlockSubmission,
)
from app.models.task_block_feedback import TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask
from app.services.task_blocks import get_dialog_tariffs, sync_blocks


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _setup(db, user_factory, *, base, block_type=BLOCK_UPLOAD, student_tariff=TARIFF_WITH_YOU,
           dialog_tariffs=(), limit=None):
    curator = user_factory(vk_id=base, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=base + 1, name="Ученик")
    student.curator_id = curator.id
    student.tariff = student_tariff
    task = TrackerTask(title="Домашка", kind="material", is_published=True, assign_to_all=True)
    db.add(task)
    db.flush()
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title="Сдать листы", dialog_reply_limit=limit,
    )
    db.add(block)
    db.flush()
    for tariff in dialog_tariffs:
        db.add(TaskBlockDialogTariff(block_id=block.id, tariff=tariff))
    submission = TaskBlockSubmission(
        block_id=block.id, user_id=student.id, submitted_at=datetime.now(timezone.utc),
    )
    db.add(submission)
    db.commit()
    return curator, student, submission


def _staff_says(client, session_factory, curator, submission, text="Разбор работы"):
    _login(client, session_factory, curator)
    with patch("app.api.task_block_feedback.notify"):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": text},
        )
    assert response.status_code == 200, response.text


def _student_says(client, session_factory, student, submission, text="Вопрос"):
    _login(client, session_factory, student)
    with patch("app.api.task_block_feedback.notify"):
        return client.post(
            f"/cabinet/task-block-submissions/{submission.id}/messages",
            data={"text": text},
        )


def _student_messages(db, submission):
    return [
        m for m in db.query(TaskBlockFeedbackMessage).all()
        if m.feedback.submission_id == submission.id and m.sender_role == "student"
    ]


def test_no_dialog_tariffs_means_reply_closed(db, user_factory, session_factory, client):
    """Пусто = ответ закрыт всем (О9) — обратно «Тарифам» блока."""
    curator, student, submission = _setup(db, user_factory, base=990_001)
    _staff_says(client, session_factory, curator, submission)

    response = _student_says(client, session_factory, student, submission)

    assert response.status_code == 403
    assert response.json()["error"] == "На эту обратную связь ответить нельзя."
    assert _student_messages(db, submission) == []
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert "На эту обратную связь ответить нельзя." in page.text
    assert 'name="text"' not in page.text


def test_tariff_outside_list_cannot_reply(db, user_factory, session_factory, client):
    curator, student, submission = _setup(
        db, user_factory, base=990_011, student_tariff=TARIFF_SELF,
        dialog_tariffs=[TARIFF_WITH_YOU, TARIFF_CONFIDENT_MAX], limit=3,
    )
    _staff_says(client, session_factory, curator, submission)

    assert _student_says(client, session_factory, student, submission).status_code == 403


def test_limit_one_allows_single_question_and_curator_answers_after(
    db, user_factory, session_factory, client,
):
    """Лимит считает только сообщения ученика (О3); куратор пишет и после (О4)."""
    curator, student, submission = _setup(
        db, user_factory, base=990_021, dialog_tariffs=[TARIFF_WITH_YOU], limit=1,
    )
    _staff_says(client, session_factory, curator, submission)

    first = _student_says(client, session_factory, student, submission, "Почему тень такая?")
    second = _student_says(client, session_factory, student, submission, "И ещё вопрос")

    assert first.status_code == 200
    assert second.status_code == 403
    assert second.json()["error"] == "Вопрос отправлен – ответ преподавателя придёт сюда."
    assert len(_student_messages(db, submission)) == 1
    _staff_says(client, session_factory, curator, submission, "Свет слева")
    _staff_says(client, session_factory, curator, submission, "И ещё пример")


def test_limit_counts_only_student_messages(db, user_factory, session_factory, client):
    curator, student, submission = _setup(
        db, user_factory, base=990_031, dialog_tariffs=[TARIFF_WITH_YOU], limit=2,
    )
    _staff_says(client, session_factory, curator, submission)
    _staff_says(client, session_factory, curator, submission, "Дополнение")

    assert _student_says(client, session_factory, student, submission).status_code == 200
    _staff_says(client, session_factory, curator, submission, "Ответ")
    assert _student_says(client, session_factory, student, submission).status_code == 200
    assert _student_says(client, session_factory, student, submission).status_code == 403


def test_student_waits_for_first_teacher_message(db, user_factory, session_factory, client):
    curator, student, submission = _setup(
        db, user_factory, base=990_041, dialog_tariffs=[TARIFF_WITH_YOU], limit=1,
    )

    response = _student_says(client, session_factory, student, submission)

    assert response.status_code == 403
    assert "после первого сообщения" in response.json()["error"]


@pytest.mark.parametrize("dialog_tariffs", [[], [TARIFF_WITH_YOU, TARIFF_SELF, TARIFF_CONFIDENT_MAX]])
def test_timed_block_reply_closed_whatever_settings(
    db, user_factory, session_factory, client, dialog_tariffs,
):
    """Контрольная на время: ответа нет на любом тарифе (созвон 00:08:51), даже
    если строки тарифов в базе каким-то путём оказались."""
    curator, student, submission = _setup(
        db, user_factory, base=990_051 + len(dialog_tariffs) * 10, block_type=BLOCK_TIMED,
        dialog_tariffs=dialog_tariffs, limit=5,
    )
    _staff_says(client, session_factory, curator, submission)

    assert _student_says(client, session_factory, student, submission).status_code == 403


def test_tariff_is_checked_at_reply_time(db, user_factory, session_factory, client):
    """Тариф берётся на момент ответа, а не сдачи (О6)."""
    curator, student, submission = _setup(
        db, user_factory, base=990_071, student_tariff=TARIFF_SELF,
        dialog_tariffs=[TARIFF_WITH_YOU], limit=1,
    )
    _staff_says(client, session_factory, curator, submission)
    assert _student_says(client, session_factory, student, submission).status_code == 403

    student.tariff = TARIFF_WITH_YOU
    db.commit()

    assert _student_says(client, session_factory, student, submission).status_code == 200


def test_staff_page_shows_what_student_can_do(db, user_factory, session_factory, client):
    curator, student, submission = _setup(
        db, user_factory, base=990_081, dialog_tariffs=[TARIFF_WITH_YOU], limit=2,
    )
    _staff_says(client, session_factory, curator, submission)

    page = client.get(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback")

    assert "Ученик может отправить ещё 2 сообщения." in page.text
    _login(client, session_factory, student)
    student_page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert "Можно отправить ещё 2 сообщения." in student_page.text
    assert 'name="text"' in student_page.text


def _task(db):
    task = TrackerTask(title="Задание", kind="material", is_published=True, assign_to_all=True)
    db.add(task)
    db.commit()
    return task


def test_sync_keeps_dialog_settings_only_on_dialog_blocks(db):
    task = _task(db)
    rows = sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_UPLOAD, "title": "Домашка",
         "dialog_tariffs": [TARIFF_WITH_YOU, "НЕТ ТАКОГО"], "dialog_reply_limit": 3},
        {"block_type": BLOCK_PHOTO_UPLOAD, "title": "Фото", "dialog_tariffs": [TARIFF_SELF]},
        {"block_type": BLOCK_TIMED, "title": "Контрольная", "time_limit_minutes": 60,
         "dialog_tariffs": [TARIFF_WITH_YOU], "dialog_reply_limit": 3},
        {"block_type": BLOCK_UPLOAD, "title": "Без ответа", "dialog_reply_limit": 4},
    ])
    db.commit()
    upload, photo, timed, closed = rows
    tariffs = get_dialog_tariffs(db, [row.id for row in rows])

    assert tariffs.get(upload.id) == {TARIFF_WITH_YOU}
    assert upload.dialog_reply_limit == 3
    # Тариф отмечен, число не задано — одно сообщение, как на созвоне.
    assert tariffs.get(photo.id) == {TARIFF_SELF}
    assert photo.dialog_reply_limit == 1
    assert timed.id not in tariffs and timed.dialog_reply_limit is None
    assert closed.id not in tariffs and closed.dialog_reply_limit is None


def test_block_item_validates_dialog_limit():
    BlockItem(block_type="upload", dialog_tariffs=[TARIFF_WITH_YOU], dialog_reply_limit=1)
    with pytest.raises(ValidationError):
        BlockItem(block_type="upload", dialog_reply_limit=0)
    with pytest.raises(ValidationError):
        BlockItem(block_type="upload", dialog_reply_limit=21)


def test_constructor_saves_and_returns_dialog_settings(
    client, db, user_factory, session_factory,
):
    """Конструктор цикла: настройка уходит в базу и возвращается в форму
    правки — иначе повторное сохранение молча закрыло бы ответ."""
    admin = user_factory(vk_id=990_091, name="Главный", is_admin=True, role_name="админ")
    _login(client, session_factory, admin)
    today = date.today()
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={"title": "Цикл", "description": None, "starts_on": today.isoformat(),
              "ends_on": (today + timedelta(days=5)).isoformat(), "is_published": True},
        headers={"X-CSRF-Token": "x"},
    )
    assert cycle.status_code == 200, cycle.text
    cycle_id = cycle.json()["cycle_id"]
    created = client.post(
        f"/cabinet/staff/program/cycles/{cycle_id}/items/material",
        json={"title": "Домашка", "description": None, "subject": None,
              "is_required": False, "starts_on": None,
              "blocks": [{"block_type": "upload", "title": "Сдать листы",
                          "dialog_tariffs": [TARIFF_WITH_YOU], "dialog_reply_limit": 7}]},
        headers={"X-CSRF-Token": "x"},
    )
    assert created.status_code == 200, created.text
    block = db.query(TaskBlock).filter_by(task_id=created.json()["task_id"]).one()
    assert get_dialog_tariffs(db, [block.id]) == {block.id: {TARIFF_WITH_YOU}}
    assert block.dialog_reply_limit == 7

    page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")
    assert '"dialog_reply_limit": 7' in page.text
    assert "function blockDialogSettingsHTML(type)" in page.text
    assert "+ blockDialogSettingsHTML(type)" in page.text
    assert "Настройка диалога" in page.text
