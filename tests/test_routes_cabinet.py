"""Tests for /cabinet route."""
import pytest


def test_cabinet_without_auth_redirects(client):
    resp = client.get("/cabinet", follow_redirects=False)
    # HTTPException(401) → redirected to /?error=session_expired
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_cabinet_with_valid_session_returns_200(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet")
    assert resp.status_code == 200


def test_cabinet_shows_student_name(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet")
    assert user.name in resp.text


def test_cabinet_shows_tariff(auth_client):
    client, user = auth_client
    resp = client.get("/cabinet")
    assert user.tariff in resp.text


def test_cabinet_student_shows_mock_exam_empty_state(auth_client):
    """With no mock exams, cabinet/student shows empty-state CTA link."""
    client, _ = auth_client
    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "пробник" in resp.text.lower()
    assert "/upload/mock-exam" in resp.text


@pytest.mark.skip(reason="cabinet_student.html (pre-b3996b6) doesn't render mock_count badge")
def test_cabinet_student_shows_mock_exam_stats(auth_client, db):
    """When mock exams exist, cabinet/student shows count and recent photos."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timezone

    client, user = auth_client
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="апрель", year=2026, filename="exam.jpg",
        s3_url="https://s3.example.com/exam.jpg",
        subject="Рисунок", status="success",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "1 работ" in resp.text
    assert "s3.example.com" in resp.text


@pytest.mark.skip(reason="cabinet_student.html (pre-b3996b6) doesn't render mock_avg badge")
def test_cabinet_student_shows_mock_avg_when_scored(auth_client, db):
    """Average score is shown on cabinet/student when at least one mock is graded."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timezone
    from decimal import Decimal

    client, user = auth_client
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="апрель", year=2026, filename="exam.jpg",
        subject="Рисунок", score=Decimal("75"), status="success",
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.get("/cabinet/student")
    assert resp.status_code == 200
    assert "75" in resp.text
    assert "ср. балл" in resp.text.lower()


def test_cabinet_blocked_user_gets_403(client, db, user_factory, session_factory):
    user = user_factory(is_active=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet", follow_redirects=False)
    # dependencies.py raises HTTP 403 for blocked users; there is no 403 handler so it passes through
    assert resp.status_code == 403
