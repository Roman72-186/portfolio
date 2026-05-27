"""Тесты редизайна 2026-05-23: диалог обратной связи + закрытие цикла."""
from datetime import date, datetime, timezone

import pytest

from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.exam_cycle import close_cycle_if_scored, has_open_cycles


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mk_cycle(db, user_id, subject="Drawing", closed=False):
    c = ExamCycle(
        user_id=user_id, subject=subject,
        started_at=date(2026, 5, 10),
        closed_at=datetime.now(timezone.utc) if closed else None,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _mk_final_work(db, user_id, cycle_id, *, score=None, attempt=1):
    w = Work(
        user_id=user_id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026,
        filename=f"final-{attempt}.jpg",
        subject="Drawing",
        status="success",
        is_final=True,
        cycle_id=cycle_id,
        attempt_number=attempt,
        score=score,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


# ── Service layer: has_open_cycles ───────────────────────────────────────────

def test_has_open_cycles_false_without_cycles(db, regular_user):
    assert has_open_cycles(db, regular_user.id) is False


def test_has_open_cycles_true_with_open_cycle(db, regular_user):
    _mk_cycle(db, regular_user.id, closed=False)
    assert has_open_cycles(db, regular_user.id) is True


def test_has_open_cycles_false_when_all_closed(db, regular_user):
    _mk_cycle(db, regular_user.id, closed=True)
    assert has_open_cycles(db, regular_user.id) is False


# ── Service layer: close_cycle_if_scored ─────────────────────────────────────

def test_close_cycle_scored_final_mock_closes_cycle(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=82)
    assert close_cycle_if_scored(db, work) is True
    db.refresh(cycle)
    assert cycle.closed_at is not None


def test_close_cycle_unscored_does_nothing(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=None)
    assert close_cycle_if_scored(db, work) is False
    db.refresh(cycle)
    assert cycle.closed_at is None


def test_close_cycle_already_closed_idempotent(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    original_closed_at = cycle.closed_at
    work = _mk_final_work(db, regular_user.id, cycle.id, score=70)
    assert close_cycle_if_scored(db, work) is False
    db.refresh(cycle)
    assert cycle.closed_at == original_closed_at


def test_close_cycle_non_final_does_nothing(db, regular_user):
    cycle = _mk_cycle(db, regular_user.id)
    w = Work(
        user_id=regular_user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026, filename="stage.jpg",
        status="success", is_final=False, cycle_id=cycle.id, score=80,
    )
    db.add(w); db.commit(); db.refresh(w)
    assert close_cycle_if_scored(db, w) is False
    db.refresh(cycle)
    assert cycle.closed_at is None


# ── Routes: /cabinet/scores удалён, /cycle/otrabotka редиректит ──────────────

def test_scores_route_removed(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/scores")
    assert resp.status_code == 404


def test_cycle_otrabotka_redirects_to_mock_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/cycle/otrabotka", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/cycle" in resp.headers["location"]


def test_cycle_probnik_redirects_to_mock_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/cycle/probnik", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/cycle" in resp.headers["location"]


def test_student_feedback_root_redirects_to_cycle_feedback_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/feedback/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/cabinet/cycle" in resp.headers["location"]
    assert "tab=feedback" in resp.headers["location"]


# ── Cycle page: feedback tab visibility ──────────────────────────────────────

def test_cycle_page_feedback_tab_hidden_without_open_cycles(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/cycle")
    assert resp.status_code == 200
    assert 'data-tab="feedback"' not in resp.text


def test_cycle_page_feedback_tab_visible_with_open_cycle(auth_client, db):
    client, user = auth_client
    _mk_cycle(db, user.id, closed=False)
    resp = client.get("/cabinet/cycle")
    assert resp.status_code == 200
    assert 'data-tab="feedback"' in resp.text


# ── Dialog message POST ──────────────────────────────────────────────────────

def test_student_cannot_send_before_staff_message(auth_client, db):
    client, user = auth_client
    cycle = _mk_cycle(db, user.id)
    work = _mk_final_work(db, user.id, cycle.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "first message from student"},
    )
    assert resp.status_code == 403


def test_admin_can_send_first_message_and_student_can_reply(
    client, admin_user, regular_user, session_factory, db
):
    """Админ пишет первым → создаётся Feedback и сообщение → студент может ответить."""
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)

    # Admin session
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "Привет, разбираем работу"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    fb = db.query(Feedback).filter(Feedback.work_id == work.id).first()
    assert fb is not None
    msgs = db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).all()
    assert len(msgs) == 1
    assert msgs[0].sender_role == "superadmin"
    assert msgs[0].text == "Привет, разбираем работу"

    # Now student replies
    student_sess = session_factory(regular_user)
    client.cookies.set("session_id", student_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "Понял, переделаю"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    msgs = db.query(FeedbackMessage).filter(FeedbackMessage.feedback_id == fb.id).order_by(FeedbackMessage.id).all()
    assert len(msgs) == 2
    assert msgs[1].sender_role == "student"


def test_message_without_text_or_photo_400(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id)
    work = _mk_final_work(db, regular_user.id, cycle.id)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "  "},  # whitespace-only and no photo
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 400


def test_message_in_closed_cycle_forbidden(
    client, admin_user, regular_user, session_factory, db
):
    cycle = _mk_cycle(db, regular_user.id, closed=True)
    work = _mk_final_work(db, regular_user.id, cycle.id, score=80)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(
        f"/cabinet/feedback/{work.id}/message",
        data={"text": "should fail"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 403
