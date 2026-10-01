"""ОС, фаза 2: «Завершить ОС», оценка 1–5, служебный топик, статистика
(созвон 30.09.2026, правила подтверждены владельцем 01.10.2026, план
`plans/2026-10-01-apparchi-call-30-09-followup.md`, О10–О26, инвариант
`docs/invariants/feedback.md`).

Правила живут в `services/feedback_rating.py`; сторожа ниже держат их через
настоящие роуты обоих диалогов — сдачи в задании и пробника."""

import asyncio
import io
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.config import settings
from app.constants import TARIFF_WITH_YOU
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback
from app.models.feedback_rating import (
    DIALOG_MOCK_EXAM,
    DIALOG_TASK_BLOCK,
    FEEDBACK_CONTROL,
    FEEDBACK_HOMEWORK,
    FEEDBACK_MOCK,
    FeedbackRating,
)
from app.models.learning_topic import LearningTopic
from app.models.notification import Notification
from app.models.task_block import (
    BLOCK_PHOTO_UPLOAD,
    BLOCK_TIMED,
    TaskBlock,
    TaskBlockDialogTariff,
    TaskBlockSubmission,
)
from app.models.task_block_feedback import TaskBlockFeedback
from app.models.tracker import TrackerTask
from app.models.work import WORK_TYPE_MOCK_EXAM, Work
from app.services import feedback_rating as rating_service


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _png(name="shot.png") -> tuple[str, bytes, str]:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return name, buffer.getvalue(), "image/png"


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    """Уведомления, S3 и Telegram — заглушки: тесты держат правила, а не сеть."""
    with patch("app.api.task_block_feedback.notify"), \
         patch("app.api.feedback.notify"), \
         patch("app.services.feedback_rating.s3_service.upload_to_s3",
               side_effect=lambda path, data, ct: f"https://s3.test/{path}"), \
         patch("app.services.feedback_rating.s3_service.delete_from_s3"), \
         patch("app.services.feedback_rating.telegram_service.send_message",
               new_callable=AsyncMock) as send:
        yield send


def _setup_block(db, user_factory, *, base, block_type=BLOCK_PHOTO_UPLOAD, dialog_tariffs=()):
    curator = user_factory(vk_id=base, name="Куратор Оценка", role_name="куратор")
    student = user_factory(vk_id=base + 1, name="Ученик Оценка")
    student.curator_id = curator.id
    student.tariff = TARIFF_WITH_YOU
    topic = LearningTopic(title="Неделя 3 октября", opens_at=date(2026, 10, 3))
    db.add(topic)
    db.flush()
    task = TrackerTask(
        title="Домашка недели", kind="material", is_published=True, assign_to_all=True,
        topic_id=topic.id,
    )
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=block_type, title="Сдать листы", dialog_reply_limit=2)
    db.add(block)
    db.flush()
    for tariff in dialog_tariffs:
        db.add(TaskBlockDialogTariff(block_id=block.id, tariff=tariff))
    submission = TaskBlockSubmission(
        block_id=block.id, user_id=student.id,
        submitted_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db.add(submission)
    db.commit()
    return curator, student, task, submission


def _staff_says(client, session_factory, curator, submission, text="Разбор работы"):
    _login(client, session_factory, curator)
    return client.post(
        f"/cabinet/staff/task-block-submissions/{submission.id}/messages", data={"text": text},
    )


def _close(client, session_factory, staff, submission):
    _login(client, session_factory, staff)
    return client.post(f"/cabinet/staff/task-block-submissions/{submission.id}/close-feedback")


def _rate(client, session_factory, student, submission, *, score="4", comment="Понятно, спасибо", files=()):
    _login(client, session_factory, student)
    return client.post(
        f"/cabinet/task-block-submissions/{submission.id}/rating",
        data={"score": score, "comment": comment},
        files=[("screenshots", f) for f in files] or None,
    )


def _rate_requests(db, student):
    return db.query(Notification).filter(
        Notification.user_id == student.id, Notification.title == "Оцени обратную связь",
    ).all()


# ── «Завершить ОС» ───────────────────────────────────────────────────────────

def test_close_needs_staff_message_first(db, user_factory, session_factory, client):
    """Без ОС нечего оценивать: кнопка не закрывает пустой диалог."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=991_001)

    response = _close(client, session_factory, curator, submission)

    assert response.status_code == 409
    assert _rate_requests(db, student) == []


def test_close_locks_dialog_for_both_and_notifies_once(db, user_factory, session_factory, client):
    """О25: после «Завершить ОС» диалог закрыт для обоих; О26: одно
    уведомление со ссылкой на диалог, повторное нажатие его не дублирует."""
    curator, student, _task, submission = _setup_block(
        db, user_factory, base=991_011, dialog_tariffs=[TARIFF_WITH_YOU],
    )
    assert _staff_says(client, session_factory, curator, submission).status_code == 200

    first = _close(client, session_factory, curator, submission)
    second = _close(client, session_factory, curator, submission)

    assert first.json() == {"ok": True, "closed_now": True}
    assert second.json() == {"ok": True, "closed_now": False}
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    db.refresh(feedback)
    assert feedback.feedback_closed_by_id == curator.id
    requests = _rate_requests(db, student)
    assert len(requests) == 1
    assert requests[0].task_block_submission_id == submission.id
    assert f"/cabinet/task-block-submissions/{submission.id}/feedback" in requests[0].text

    staff_after = _staff_says(client, session_factory, curator, submission, "Ещё одно")
    assert staff_after.status_code == 403
    _login(client, session_factory, student)
    student_after = client.post(
        f"/cabinet/task-block-submissions/{submission.id}/messages", data={"text": "Вопрос"},
    )
    assert student_after.status_code == 403
    assert "завершил" in student_after.json()["error"]


def test_rating_form_appears_only_after_close(db, user_factory, session_factory, client):
    """О10/О14б: форма оценки — только после «Завершить ОС»."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=991_021)
    _staff_says(client, session_factory, curator, submission)
    page_url = f"/cabinet/task-block-submissions/{submission.id}/feedback"

    _login(client, session_factory, student)
    assert "data-rating-form" not in client.get(page_url).text
    assert _rate(client, session_factory, student, submission).status_code == 403

    _close(client, session_factory, curator, submission)
    _login(client, session_factory, student)
    assert "data-rating-form" in client.get(page_url).text


# ── Оценка ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("score, comment", [("4", ""), ("4", "   "), ("0", "Норм"), ("6", "Норм"), ("", "Норм")])
def test_rating_needs_score_in_range_and_comment(
    db, user_factory, session_factory, client, score, comment,
):
    """О12, О23: комментарий обязателен при любой оценке, оценка 1–5."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=991_031)
    _staff_says(client, session_factory, curator, submission)
    _close(client, session_factory, curator, submission)

    response = _rate(client, session_factory, student, submission, score=score, comment=comment)

    assert response.status_code == 422
    assert db.query(FeedbackRating).count() == 0


def test_rating_is_final_and_goes_to_closer(db, user_factory, session_factory, client):
    """О14, О24: одна оценка на диалог, изменить нельзя. Оценивают того, кто
    нажал «Завершить ОС», вид ОС — из типа блока (О16)."""
    curator, student, task, submission = _setup_block(
        db, user_factory, base=991_041, block_type=BLOCK_TIMED,
    )
    _staff_says(client, session_factory, curator, submission)
    _close(client, session_factory, curator, submission)

    first = _rate(client, session_factory, student, submission, score="5", comment="Всё понятно")
    second = _rate(client, session_factory, student, submission, score="1", comment="Передумал")

    assert first.status_code == 200
    assert second.status_code == 409
    rating = db.query(FeedbackRating).one()
    assert (rating.score, rating.comment) == (5, "Всё понятно")
    assert rating.dialog_kind == DIALOG_TASK_BLOCK
    assert rating.curator_id == curator.id
    assert rating.feedback_type == FEEDBACK_CONTROL
    assert rating.task_id == task.id


def test_rating_takes_up_to_three_screenshots(db, user_factory, session_factory, client):
    """О13: до трёх скриншотов к комментарию."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=991_051)
    _staff_says(client, session_factory, curator, submission)
    _close(client, session_factory, curator, submission)

    too_many = _rate(
        client, session_factory, student, submission, files=[_png(f"{i}.png") for i in range(4)],
    )
    assert too_many.status_code == 422
    ok = _rate(client, session_factory, student, submission, files=[_png("a.png"), _png("b.png")])
    assert ok.status_code == 200
    rating = db.query(FeedbackRating).one()
    assert rating.feedback_type == FEEDBACK_HOMEWORK
    assert len(rating.images) == 2


def test_staff_sees_rating_comment_in_dialog(db, user_factory, session_factory, client):
    """О15: куратор видит цифру и комментарий в своём диалоге."""
    curator, student, _task, submission = _setup_block(db, user_factory, base=991_061)
    _staff_says(client, session_factory, curator, submission)
    _close(client, session_factory, curator, submission)
    _rate(client, session_factory, student, submission, score="3", comment="Мало примеров")

    _login(client, session_factory, curator)
    page = client.get(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback").text

    assert "3 из 5" in page
    assert "Мало примеров" in page
    assert "Завершить ОС" not in page


# ── Служебный топик ──────────────────────────────────────────────────────────

def test_rating_goes_to_care_topic_with_thread(
    db, user_factory, session_factory, client, monkeypatch, _quiet_side_effects,
):
    """О17, О18: каждая оценка — одно сообщение в топик служебной группы."""
    monkeypatch.setattr(settings, "telegram_care_chat_id", -100500)
    monkeypatch.setattr(settings, "telegram_care_thread_id", 77)
    curator, student, _task, submission = _setup_block(db, user_factory, base=991_071)
    _staff_says(client, session_factory, curator, submission)
    _close(client, session_factory, curator, submission)

    assert _rate(client, session_factory, student, submission, comment="<b>Спасибо</b>").status_code == 200

    send = _quiet_side_effects
    send.assert_awaited_once()
    args, kwargs = send.call_args
    assert args[0] == -100500
    assert kwargs["message_thread_id"] == 77
    text = args[1]
    for part in ("Оценка ОС: 4 из 5", "Ученик Оценка", TARIFF_WITH_YOU, "Куратор Оценка",
                 "Домашка: Домашка недели", "&lt;b&gt;Спасибо&lt;/b&gt;"):
        assert part in text, part
    assert f"/cabinet/staff/task-block-submissions/{submission.id}/feedback" in text


def test_care_topic_skipped_without_env(db, user_factory, session_factory, client, _quiet_side_effects):
    """Без `TELEGRAM_CARE_CHAT_ID` — пропуск, оценка всё равно сохранена."""
    assert settings.telegram_care_chat_id == 0
    curator, student, _task, submission = _setup_block(db, user_factory, base=991_081)
    _staff_says(client, session_factory, curator, submission)
    _close(client, session_factory, curator, submission)

    assert _rate(client, session_factory, student, submission).status_code == 200

    _quiet_side_effects.assert_not_awaited()
    assert db.query(FeedbackRating).count() == 1


def test_telegram_failure_does_not_lose_rating(db, user_factory, monkeypatch, _quiet_side_effects):
    monkeypatch.setattr(settings, "telegram_care_chat_id", -100500)
    _quiet_side_effects.return_value = False
    student = user_factory(vk_id=991_091, name="Ученик Сбой")
    rating = FeedbackRating(
        dialog_kind=DIALOG_TASK_BLOCK, dialog_id=1, student_id=student.id,
        feedback_type=FEEDBACK_HOMEWORK, score=4, comment="ok",
    )
    db.add(rating)
    db.commit()

    asyncio.run(rating_service.send_rating_to_care_topic(rating.id, "/x"))

    assert db.query(FeedbackRating).count() == 1


# ── Пробник ──────────────────────────────────────────────────────────────────

def _mock_dialog(db, user_factory, *, base):
    curator = user_factory(vk_id=base, name="Куратор Пробник", role_name="куратор")
    student = user_factory(vk_id=base + 1, name="Ученик Пробник")
    student.curator_id = curator.id
    cycle = ExamCycle(user_id=student.id, subject="Рисунок", started_at=date(2026, 10, 1))
    db.add(cycle)
    db.flush()
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="10", year=2026,
        filename="final.jpg", subject="Рисунок", status="success",
        s3_url="https://example.test/final.jpg", is_final=True, cycle_id=cycle.id,
        attempt_number=1,
    )
    db.add(work)
    db.commit()
    return curator, student, cycle, work


def test_mock_exam_close_and_rate(db, user_factory, session_factory, client):
    """Одно правило на все виды ОС (О25): пробник закрывается той же кнопкой,
    оценка одна, вид ОС — «Пробник»."""
    curator, student, cycle, work = _mock_dialog(db, user_factory, base=991_101)
    _login(client, session_factory, curator)
    message_url = f"/cabinet/feedback/{work.id}/message"
    headers = {"Accept": "application/json"}
    assert client.post(message_url, data={"text": "Разбор"}, headers=headers).status_code == 200
    assert client.post(f"/cabinet/feedback/{work.id}/close-feedback").json()["closed_now"] is True
    assert client.post(message_url, data={"text": "Ещё"}, headers=headers).status_code == 403
    assert "Обратная связь завершена" in client.get(f"/cabinet/curator/feedback/{cycle.id}").text

    _login(client, session_factory, student)
    assert "data-rating-form" in client.get(f"/cabinet/feedback/{cycle.id}").text
    rate_url = f"/cabinet/feedback/{work.id}/rating"
    assert client.post(rate_url, data={"score": "5", "comment": "Супер"}).status_code == 200
    assert client.post(rate_url, data={"score": "2", "comment": "Нет"}).status_code == 409

    rating = db.query(FeedbackRating).one()
    feedback = db.query(Feedback).filter_by(work_id=work.id).one()
    assert (rating.dialog_kind, rating.dialog_id) == (DIALOG_MOCK_EXAM, feedback.id)
    assert rating.feedback_type == FEEDBACK_MOCK
    assert rating.curator_id == curator.id
    assert len(_rate_requests(db, student)) == 1


def test_mock_exam_rating_before_close_is_refused(db, user_factory, session_factory, client):
    curator, student, _cycle, work = _mock_dialog(db, user_factory, base=991_111)
    _login(client, session_factory, curator)
    client.post(f"/cabinet/feedback/{work.id}/message", data={"text": "Разбор"},
                headers={"Accept": "application/json"})

    _login(client, session_factory, student)
    response = client.post(f"/cabinet/feedback/{work.id}/rating", data={"score": "5", "comment": "ok"})

    assert response.status_code == 403
    assert client.post(f"/cabinet/feedback/{work.id}/close-feedback").status_code == 403


# ── Статистика ───────────────────────────────────────────────────────────────

def test_stats_average_per_task_and_time_to_first_feedback(db, user_factory, session_factory, client):
    """О19: средняя куратора по каждому заданию цикла с числом оценок;
    О21: время до первой ОС считается и по сдачам в заданиях."""
    from app.services.activity_stats import get_feedback_curator_stats, get_feedback_rating_by_task

    curator, student, task, submission = _setup_block(db, user_factory, base=991_121)
    second_student = user_factory(vk_id=991_123, name="Ученик Второй")
    second_student.curator_id = curator.id
    second = TaskBlockSubmission(
        block_id=submission.block_id, user_id=second_student.id,
        submitted_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(second)
    db.commit()
    for who, sub, score in ((student, submission, "5"), (second_student, second, "2")):
        _staff_says(client, session_factory, curator, sub)
        _close(client, session_factory, curator, sub)
        assert _rate(client, session_factory, who, sub, score=score).status_code == 200

    by_task = get_feedback_rating_by_task(db)
    assert len(by_task) == 1
    row = by_task[0]
    assert (row["ratings"], row["avg_rating"]) == (2, 3.5)
    assert row["task_title"] == task.title
    assert row["topic_title"] == "Неделя 3 октября"
    assert row["feedback_label"] == "Домашка"

    curator_row = next(r for r in get_feedback_curator_stats(db) if r["curator_id"] == curator.id)
    assert curator_row["dialogs"] == 2
    assert curator_row["avg_rating"] == 3.5
    assert curator_row["avg_first_response_seconds"] >= 3600


def test_staff_activity_counts_feedback_given(db, user_factory, session_factory, client):
    """О21: у кураторов «оценено» заменено на «дал ОС»."""
    from app.services.activity_stats import get_staff_activity

    curator, _student, _task, submission = _setup_block(db, user_factory, base=991_131)
    _staff_says(client, session_factory, curator, submission)
    _staff_says(client, session_factory, curator, submission, "Ещё")

    row = next(r for r in get_staff_activity(db) if r["user_id"] == curator.id)
    assert row["feedback_given"] == 1
