"""Tests for new split-panel curator routes added in 2026-04-13."""
from datetime import datetime, timezone

import pytest

from app.models.mock_exam_lock import MockExamLock
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM
from app.models.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def curator_client(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=800001, name="Curator", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    return client, curator


@pytest.fixture()
def other_curator_client(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=800099, name="Other Curator", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    return client, curator


@pytest.fixture()
def student(db, user_factory, curator_client):
    _, curator = curator_client
    student = user_factory(vk_id=800002, name="Student One", role_name="ученик")
    student.curator_id = curator.id
    student.first_name = "Анна"
    student.last_name = "Иванова"
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def _add_work(db, user_id, work_type, month="январь", year=2026, score=None,
              subject=None, status="success"):
    w = Work(
        user_id=user_id,
        work_type=work_type,
        month=month,
        year=year,
        filename="test.jpg",
        s3_url="https://s3.example.com/test.jpg",
        status=status,
        score=score,
        subject=subject,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


# ---------------------------------------------------------------------------
# GET /cabinet/curator — dashboard
# ---------------------------------------------------------------------------

def test_curator_dashboard_redirects_to_students(curator_client):
    client, _ = curator_client
    resp = client.get("/cabinet/curator", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/students"


def test_curator_dashboard_lands_on_student_list(curator_client, student):
    client, _ = curator_client
    resp = client.get("/cabinet/curator")  # follows redirect → /cabinet/students
    assert resp.status_code == 200
    assert "Иванова" in resp.text or "Анна" in resp.text


def test_curator_dashboard_denied_for_student(client, db, user_factory, session_factory):
    user = user_factory(vk_id=800010, name="Student", role_name="ученик")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/curator", follow_redirects=False)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /cabinet/curator/portfolio
# ---------------------------------------------------------------------------

def test_curator_portfolio_page_loads(curator_client):
    client, _ = curator_client
    resp = client.get("/cabinet/curator/portfolio")
    assert resp.status_code == 200


def test_curator_portfolio_page_has_sidebar(curator_client, student):
    client, _ = curator_client
    resp = client.get("/cabinet/curator/portfolio")
    assert resp.status_code == 200
    # Sidebar должен содержать имя студента
    assert "Иванова" in resp.text or "Анна" in resp.text


# ---------------------------------------------------------------------------
# GET /cabinet/curator/portfolio/student/{id} — JSON
# ---------------------------------------------------------------------------

def test_portfolio_student_json_returns_json(curator_client, db, student):
    client, _ = curator_client
    _add_work(db, student.id, WORK_TYPE_BEFORE)
    _add_work(db, student.id, WORK_TYPE_AFTER)

    resp = client.get(f"/cabinet/curator/portfolio/student/{student.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "student" in data
    assert "before_works" in data
    assert "after_by_month" in data


def test_portfolio_student_json_correct_name(curator_client, db, student):
    client, _ = curator_client
    resp = client.get(f"/cabinet/curator/portfolio/student/{student.id}")
    data = resp.json()
    assert "Иванова" in data["student"]["name"] or "Анна" in data["student"]["name"]


def test_portfolio_student_json_contains_works(curator_client, db, student):
    client, _ = curator_client
    _add_work(db, student.id, WORK_TYPE_BEFORE)
    _add_work(db, student.id, WORK_TYPE_BEFORE)

    resp = client.get(f"/cabinet/curator/portfolio/student/{student.id}")
    data = resp.json()
    assert len(data["before_works"]) == 2


def test_portfolio_student_access_denied_other_curator(client, db, user_factory, session_factory):
    # Curator 1 owns the student
    cur1 = user_factory(vk_id=810001, name="Curator1", role_name="куратор")
    student = user_factory(vk_id=810002, name="Student", role_name="ученик")
    student.curator_id = cur1.id
    db.add(student)
    db.commit()

    # Curator 2 tries to access
    cur2 = user_factory(vk_id=810003, name="Curator2", role_name="куратор")
    sess2 = session_factory(cur2)
    client.cookies.set("session_id", sess2.id)

    resp = client.get(f"/cabinet/curator/portfolio/student/{student.id}",
                      follow_redirects=False)
    assert resp.status_code == 403


def test_portfolio_student_not_found_404(curator_client):
    client, _ = curator_client
    resp = client.get("/cabinet/curator/portfolio/student/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /cabinet/curator/mock-exams
# ---------------------------------------------------------------------------

def test_curator_mock_exams_page_loads(curator_client):
    client, _ = curator_client
    resp = client.get("/cabinet/curator/mock-exams")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /cabinet/curator/mock-exams/student/{id} — JSON
# ---------------------------------------------------------------------------

def test_mock_exams_student_json_returns_json(curator_client, db, student):
    client, _ = curator_client
    _add_work(db, student.id, WORK_TYPE_MOCK_EXAM, subject="Рисунок", score=75)

    resp = client.get(f"/cabinet/curator/mock-exams/student/{student.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "student" in data
    assert "mock_works" in data
    assert "mock_locks" in data


def test_mock_exams_student_json_avg_score(curator_client, db, student):
    client, _ = curator_client
    _add_work(db, student.id, WORK_TYPE_MOCK_EXAM, subject="Рисунок", score=80)
    _add_work(db, student.id, WORK_TYPE_MOCK_EXAM, subject="Рисунок", score=60)

    resp = client.get(f"/cabinet/curator/mock-exams/student/{student.id}")
    data = resp.json()
    assert data["student"]["avg_score"] == 70


def test_mock_exams_works_grouped_by_subject(curator_client, db, student):
    client, _ = curator_client
    _add_work(db, student.id, WORK_TYPE_MOCK_EXAM, subject="Рисунок", score=80)
    _add_work(db, student.id, WORK_TYPE_MOCK_EXAM, subject="Композиция", score=90)

    resp = client.get(f"/cabinet/curator/mock-exams/student/{student.id}")
    data = resp.json()
    assert "Рисунок" in data["mock_works"]
    assert "Композиция" in data["mock_works"]


def test_mock_exams_access_denied_other_curator(client, db, user_factory, session_factory):
    # Curator 1 owns the student
    cur1 = user_factory(vk_id=820001, name="CuratorA", role_name="куратор")
    student = user_factory(vk_id=820002, name="StudentB", role_name="ученик")
    student.curator_id = cur1.id
    db.add(student)
    db.commit()

    # Curator 2 tries to access
    cur2 = user_factory(vk_id=820003, name="CuratorC", role_name="куратор")
    sess2 = session_factory(cur2)
    client.cookies.set("session_id", sess2.id)

    resp = client.get(f"/cabinet/curator/mock-exams/student/{student.id}",
                      follow_redirects=False)
    assert resp.status_code == 403


def test_mock_exams_not_found_404(curator_client):
    client, _ = curator_client
    resp = client.get("/cabinet/curator/mock-exams/student/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /cabinet/mock-exam/unlock
# ---------------------------------------------------------------------------

def test_curator_unlock_missing_student_keeps_403_contract(curator_client):
    client, _ = curator_client

    resp = client.post(
        "/cabinet/mock-exam/unlock",
        data={"student_id": 999999, "subject": "Рисунок"},
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert "Нет доступа к этому студенту" in resp.text


def test_curator_unlock_foreign_student_keeps_403_contract(
    client, db, user_factory, session_factory
):
    owner = user_factory(vk_id=820004, name="Owner", role_name="куратор")
    other = user_factory(vk_id=820005, name="Other", role_name="куратор")
    student = user_factory(vk_id=820006, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()

    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)

    resp = client.post(
        "/cabinet/mock-exam/unlock",
        data={"student_id": student.id, "subject": "Рисунок"},
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert "Нет доступа к этому студенту" in resp.text


def test_curator_unlock_invalid_subject_still_returns_400(curator_client, student):
    client, _ = curator_client

    resp = client.post(
        "/cabinet/mock-exam/unlock",
        data={"student_id": student.id, "subject": "Скульптура"},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "Неверный предмет" in resp.text


def test_curator_unlock_own_student_clears_lock(curator_client, db, student):
    client, curator = curator_client
    lock = MockExamLock(
        user_id=student.id,
        subject="Рисунок",
        is_locked=True,
        locked_at=datetime.now(timezone.utc),
    )
    db.add(lock)
    db.commit()
    db.refresh(lock)

    resp = client.post(
        "/cabinet/mock-exam/unlock",
        data={"student_id": student.id, "subject": "Рисунок"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    db.refresh(lock)
    assert lock.is_locked is False
    assert lock.unlocked_by_id == curator.id


# ---------------------------------------------------------------------------
# POST /cabinet/students/{id}/profile — admin edits student tags
# ---------------------------------------------------------------------------

def _as_admin(client, user_factory, session_factory, vk_id=820100):
    admin = user_factory(vk_id=vk_id, name="Admin", role_name="админ")
    sess = session_factory(admin)
    client.cookies.set("session_id", sess.id)
    return admin


def test_admin_sets_cohort_tag_on_student_profile(client, db, user_factory, session_factory, student):
    _as_admin(client, user_factory, session_factory)

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data={
            "first_name": student.first_name,
            "last_name": student.last_name,
            "phone": "+79990001122",
            "cohort_tag": "june",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    db.refresh(student)
    assert student.cohort_tag == "june"


def test_admin_clears_cohort_tag_on_student_profile(client, db, user_factory, session_factory, student):
    _as_admin(client, user_factory, session_factory, vk_id=820101)
    student.cohort_tag = "may"
    db.add(student)
    db.commit()

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data={
            "first_name": student.first_name,
            "last_name": student.last_name,
            "phone": "+79990001122",
            "cohort_tag": "",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    db.refresh(student)
    assert student.cohort_tag is None


def test_admin_sets_invalid_cohort_tag_returns_error(client, user_factory, session_factory, student):
    _as_admin(client, user_factory, session_factory, vk_id=820102)

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data={
            "first_name": student.first_name,
            "last_name": student.last_name,
            "phone": "+79990001122",
            "cohort_tag": "winter",
        },
    )

    assert resp.status_code == 400
    assert "Неверная метка набора" in resp.json()["errors"]
