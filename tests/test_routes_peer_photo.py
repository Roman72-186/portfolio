"""Тесты GET /cabinet/api/portfolio/peer-photo/{work_id} — анонимная подборка
по баллу в Портфолио → Пробные экзамены (владелец 11.09.2026).

Ключевой тест здесь — граница анонимности: без своего оценённого финала по
тому же билету доступа к чужому фото нет (404), и даже в успешном ответе
никакие идентифицирующие данные автора (VK ID, сырой S3-путь) не всплывают
в теле ответа.
"""
from datetime import date
from unittest.mock import patch

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.work import Work, WORK_TYPE_MOCK_EXAM


def _mk_ticket(db, *, subject="Рисунок", creator_id):
    assignment = ExamAssignment(
        title="Пробник", subject=subject, created_by_id=creator_id, status="published",
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=1, title="Билет",
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), assign_to_all=True,
    )
    db.add(ticket)
    db.flush()
    return ticket


def _mk_scored_final(db, *, user_id, ticket_id, subject="Рисунок", score=70, s3_path="probniki/999/1/attempt-1/final/a.jpg"):
    cycle = ExamCycle(user_id=user_id, subject=subject, ticket_id=ticket_id, started_at=date(2026, 1, 5))
    db.add(cycle)
    db.flush()
    w = Work(
        user_id=user_id, work_type=WORK_TYPE_MOCK_EXAM, month="01", year=2026,
        filename="final.jpg", subject=subject, status="success",
        s3_path=s3_path, cycle_id=cycle.id, is_final=True, attempt_number=1, score=score,
    )
    db.add(w)
    db.commit()
    return cycle, w


def test_peer_photo_requires_auth(client, db, regular_user, user_factory):
    other = user_factory(vk_id=820_001, name="Other")
    ticket = _mk_ticket(db, creator_id=other.id)
    _, peer_work = _mk_scored_final(db, user_id=other.id, ticket_id=ticket.id)

    resp = client.get(f"/cabinet/api/portfolio/peer-photo/{peer_work.id}", follow_redirects=False)
    assert resp.status_code == 302


def test_peer_photo_200_for_entitled_viewer(auth_client, db, user_factory):
    client, viewer = auth_client
    other = user_factory(vk_id=820_002, name="Other")
    ticket = _mk_ticket(db, creator_id=other.id)
    _, peer_work = _mk_scored_final(
        db, user_id=other.id, ticket_id=ticket.id,
        s3_path="probniki/820002/1/attempt-1/final/secret.jpg", score=65,
    )
    _mk_scored_final(db, user_id=viewer.id, ticket_id=ticket.id, score=70)

    with patch("app.api.cabinet_student.s3_service.download_from_s3", return_value=b"fake-bytes") as mock_dl:
        resp = client.get(f"/cabinet/api/portfolio/peer-photo/{peer_work.id}")

    assert resp.status_code == 200
    assert resp.content == b"fake-bytes"
    assert resp.headers["content-type"].startswith("image/")
    mock_dl.assert_called_once_with("probniki/820002/1/attempt-1/final/secret.jpg")


def test_peer_photo_prefers_feedback_message_photo_over_work_path(auth_client, db, user_factory):
    """Тот же приоритет, что и для своей карточки: последнее фото ученика
    из диалога ОС важнее исходной загрузки Work.s3_path."""
    client, viewer = auth_client
    other = user_factory(vk_id=820_003, name="Other")
    ticket = _mk_ticket(db, creator_id=other.id)
    _, peer_work = _mk_scored_final(
        db, user_id=other.id, ticket_id=ticket.id,
        s3_path="probniki/820003/1/attempt-1/final/original.jpg", score=65,
    )
    _mk_scored_final(db, user_id=viewer.id, ticket_id=ticket.id, score=70)

    fb = Feedback(work_id=peer_work.id, curator_id=other.id)
    db.add(fb)
    db.flush()
    db.add(FeedbackMessage(
        feedback_id=fb.id, sender_id=other.id, sender_role="student",
        photo_s3_path="probniki/820003/1/attempt-1/final/resubmit.jpg",
        photo_s3_url="https://cdn.example/resubmit.jpg",
    ))
    db.commit()

    with patch("app.api.cabinet_student.s3_service.download_from_s3", return_value=b"x") as mock_dl:
        resp = client.get(f"/cabinet/api/portfolio/peer-photo/{peer_work.id}")

    assert resp.status_code == 200
    mock_dl.assert_called_once_with("probniki/820003/1/attempt-1/final/resubmit.jpg")


def test_peer_photo_404_without_shared_ticket(auth_client, db, user_factory):
    """Смотрящий не сдавал этот билет вообще (нет своего оценённого финала
    по тому же ticket_id) — 404, VK ID/путь автора не должны всплывать в ответе."""
    client, viewer = auth_client
    other = user_factory(vk_id=820_004, name="Other")
    ticket = _mk_ticket(db, creator_id=other.id)
    _, peer_work = _mk_scored_final(
        db, user_id=other.id, ticket_id=ticket.id,
        s3_path="probniki/820004/1/attempt-1/final/hidden.jpg", score=65,
    )
    # viewer НЕ сдавал этот билет — нет своего Work с тем же ticket_id.

    resp = client.get(f"/cabinet/api/portfolio/peer-photo/{peer_work.id}")

    assert resp.status_code == 404
    body = resp.text
    assert "820004" not in body
    assert "hidden.jpg" not in body
    assert "probniki/" not in body


def test_peer_photo_404_for_unscored_work(auth_client, db, user_factory):
    client, viewer = auth_client
    other = user_factory(vk_id=820_005, name="Other")
    ticket = _mk_ticket(db, creator_id=other.id)
    _mk_scored_final(db, user_id=viewer.id, ticket_id=ticket.id, score=70)
    cycle = ExamCycle(user_id=other.id, subject="Рисунок", ticket_id=ticket.id, started_at=date(2026, 1, 5))
    db.add(cycle)
    db.flush()
    unscored = Work(
        user_id=other.id, work_type=WORK_TYPE_MOCK_EXAM, month="01", year=2026,
        filename="unscored.jpg", subject="Рисунок", status="success",
        s3_path="probniki/820005/1/attempt-1/final/unscored.jpg",
        cycle_id=cycle.id, is_final=True, attempt_number=1, score=None,
    )
    db.add(unscored)
    db.commit()

    resp = client.get(f"/cabinet/api/portfolio/peer-photo/{unscored.id}")
    assert resp.status_code == 404


def test_peer_photo_404_for_ticketless_cycle(auth_client, db, user_factory):
    client, viewer = auth_client
    other = user_factory(vk_id=820_006, name="Other")
    cycle = ExamCycle(user_id=other.id, subject="Рисунок", ticket_id=None, started_at=date(2026, 1, 5))
    db.add(cycle)
    db.flush()
    legacy = Work(
        user_id=other.id, work_type=WORK_TYPE_MOCK_EXAM, month="01", year=2026,
        filename="legacy.jpg", subject="Рисунок", status="success",
        s3_path="probniki/820006/1/attempt-1/final/legacy.jpg",
        cycle_id=cycle.id, is_final=True, attempt_number=1, score=70,
    )
    db.add(legacy)
    db.commit()

    resp = client.get(f"/cabinet/api/portfolio/peer-photo/{legacy.id}")
    assert resp.status_code == 404


def test_peer_photo_404_for_intermediate_work(auth_client, db, user_factory):
    client, viewer = auth_client
    other = user_factory(vk_id=820_007, name="Other")
    ticket = _mk_ticket(db, creator_id=other.id)
    cycle, final = _mk_scored_final(db, user_id=other.id, ticket_id=ticket.id, score=65)
    _mk_scored_final(db, user_id=viewer.id, ticket_id=ticket.id, score=70)
    stage = Work(
        user_id=other.id, work_type=WORK_TYPE_MOCK_EXAM, month="01", year=2026,
        filename="stage.jpg", subject="Рисунок", status="success",
        s3_path="probniki/820007/1/attempt-1/intermediate/stage.jpg",
        cycle_id=cycle.id, is_final=False, parent_work_id=final.id,
    )
    db.add(stage)
    db.commit()

    resp = client.get(f"/cabinet/api/portfolio/peer-photo/{stage.id}")
    assert resp.status_code == 404
