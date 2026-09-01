"""Единый экран проверки: список учеников + карточка ученика по всем доменам.

`plans/2026-09-01-apparchi-student-centric-review.md`, этап 5.
"""
from datetime import date, datetime, timezone

from app.models.exam_cycle import ExamCycle
from app.models.work import Work, WORK_TYPE_MOCK_EXAM


def _work(db, user_id, *, score=None, created_at=None):
    w = Work(
        user_id=user_id, work_type=WORK_TYPE_MOCK_EXAM, month="сентябрь", year=2026,
        filename="final.jpg", s3_url="https://s3.example.com/final.jpg",
        subject="Рисунок", status="success", is_final=True, score=score,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    if created_at is not None:
        w.created_at = created_at
        db.commit()
    return w


def test_student_list_shows_own_students_with_counts(auth_client, db, user_factory, session_factory):
    curator = user_factory(vk_id=860_101, name="Куратор", role_name="куратор")
    _, student = auth_client
    student.curator_id = curator.id
    db.commit()
    _work(db, student.id, score=None)

    client, _ = auth_client
    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.get("/cabinet/staff/students-review")

    assert resp.status_code == 200
    assert student.name in resp.text
    assert "1 непроверено" in resp.text


def test_student_list_excludes_foreign_students(db, user_factory, session_factory, client):
    own_curator = user_factory(vk_id=860_102, name="Куратор своя", role_name="куратор")
    own_student = user_factory(vk_id=860_103, name="Свой ученик")
    own_student.curator_id = own_curator.id
    foreign_student = user_factory(vk_id=860_104, name="Чужой ученик")
    db.commit()

    client.cookies.set("session_id", session_factory(own_curator).id)
    resp = client.get("/cabinet/staff/students-review")

    assert own_student.name in resp.text
    assert foreign_student.name not in resp.text


def test_student_detail_shows_items_across_domains(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_105, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_106, name="Ученик Всё Сдал")
    student.curator_id = curator.id
    db.commit()
    _work(db, student.id, score=None)
    cycle = ExamCycle(
        user_id=student.id, subject="Композиция", started_at=date.today(),
    )
    db.add(cycle)
    db.commit()

    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.get(f"/cabinet/staff/students-review/{student.id}")

    assert resp.status_code == 200
    assert "Пробник" in resp.text
    assert "Композиция" in resp.text


def test_curator_cannot_open_foreign_student_review(db, user_factory, session_factory, client):
    owner = user_factory(vk_id=860_107, name="Куратор своя", role_name="куратор")
    other = user_factory(vk_id=860_108, name="Куратор чужая", role_name="куратор")
    student = user_factory(vk_id=860_109, name="Ученик")
    student.curator_id = owner.id
    db.commit()

    client.cookies.set("session_id", session_factory(other).id)
    resp = client.get(f"/cabinet/staff/students-review/{student.id}")

    assert resp.status_code == 403


def test_student_cannot_open_review_screen(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/staff/students-review")
    assert resp.status_code == 403


def test_curator_can_mark_work_viewed_without_scoring(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_112, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_113, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    work = _work(db, student.id, score=None)

    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.post(f"/cabinet/staff/students-review/work/{work.id}/viewed")

    assert resp.status_code == 200
    db.refresh(work)
    assert work.viewed_at is not None
    assert work.viewed_by_id == curator.id
    assert work.score is None  # «просмотрено» не ставит балл


def test_curator_cannot_mark_foreign_work_viewed(db, user_factory, session_factory, client):
    owner = user_factory(vk_id=860_114, name="Куратор своя", role_name="куратор")
    other = user_factory(vk_id=860_115, name="Куратор чужая", role_name="куратор")
    student = user_factory(vk_id=860_116, name="Ученик")
    student.curator_id = owner.id
    db.commit()
    work = _work(db, student.id, score=None)

    client.cookies.set("session_id", session_factory(other).id)
    resp = client.post(f"/cabinet/staff/students-review/work/{work.id}/viewed")

    assert resp.status_code == 403
    db.refresh(work)
    assert work.viewed_at is None


def test_curator_can_mark_cycle_viewed_without_closing(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_117, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_118, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    cycle = ExamCycle(user_id=student.id, subject="Рисунок", started_at=date.today())
    db.add(cycle)
    db.commit()

    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.post(f"/cabinet/staff/students-review/cycle/{cycle.id}/viewed")

    assert resp.status_code == 200
    db.refresh(cycle)
    assert cycle.viewed_at is not None
    assert cycle.closed_at is None  # «просмотрено» не закрывает цикл


def test_week_filter_excludes_items_outside_range(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_110, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_111, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    _work(db, student.id, score=None, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.get(f"/cabinet/staff/students-review/{student.id}?week=2026-09-03")

    assert resp.status_code == 200
    assert "За эту неделю по фильтрам ничего не сдано" in resp.text
