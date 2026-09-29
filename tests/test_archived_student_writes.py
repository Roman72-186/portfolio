"""Архивный и удалённый ученик закрыты на запись во всех маршрутах сотрудника.

Код-ревью 28.09.2026, P2 № 12: оценка, пересдача, доработка, разблокировка
и удаление работы в карточке ученика не звали `_check_access`, а маршруты
кураторского и админского кабинета (`/cabinet/mock-exam/unlock`,
`/cabinet/works/{id}/score`, `/cabinet/admin/works/{id}/score`) не проверяли
состояние ученика вовсе. Архив прошлого потока правился задним числом, удалённому
ученику уходили уведомления. Правило трёх состояний — `AGENTS.md`, правило 8.

Данные собраны так, чтобы до правки каждый маршрут доходил до записи: работа —
финал пробника с `sent_to_retake` по открытому циклу, лок пробника заперт.
Иначе маршрут отказал бы раньше проверки ученика, и тест был бы зелёным зря.
"""
from datetime import date

import pytest

from app.models.exam_cycle import ExamCycle
from app.models.mock_exam_lock import MockExamLock
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.user_management import archive_user, soft_delete_user

SUBJECT = "Рисунок"

# (метод, адрес, данные формы, код отказа)
ROUTES = [
    ("post", "/cabinet/students/{sid}/retakes/{wid}/subject", {"subject": "Композиция"}, 404),
    ("post", "/cabinet/students/{sid}/works/{wid}/score", {"score": "80", "comment": "x"}, 404),
    ("post", "/cabinet/students/{sid}/mock-exams/{wid}/retake", {"score": "40", "comment": "x"}, 404),
    ("post", "/cabinet/students/{sid}/mock-exams/{wid}/revision", {}, 404),
    ("post", "/cabinet/students/{sid}/mock-exams/unlock", {"subject": SUBJECT}, 404),
    ("delete", "/cabinet/students/{sid}/works/{wid}", None, 404),
    ("post", "/cabinet/mock-exam/unlock", {"student_id": "{sid}", "subject": SUBJECT}, 403),
    ("post", "/cabinet/works/{wid}/score", {"score": "80", "comment": "x"}, 404),
    ("post", "/cabinet/admin/works/{wid}/score", {"score": "80", "comment": "x"}, 404),
]


def _archive(db, student_id, actor_id):
    assert archive_user(db, target_user_id=student_id, performed_by_id=actor_id) is True


def _soft_delete(db, student_id, actor_id):
    assert soft_delete_user(db, target_user_id=student_id, performed_by_id=actor_id) is True


@pytest.fixture()
def setup(db, user_factory, session_factory, client):
    actor = user_factory(vk_id=940_001, name="Супер", role_name="суперадмин")
    student = user_factory(vk_id=940_002, name="Ученик Прошлого Потока")

    cycle = ExamCycle(user_id=student.id, subject=SUBJECT, started_at=date(2026, 5, 10))
    db.add(cycle)
    db.commit()
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="05", year=2026,
        filename="final.jpg",
        subject=SUBJECT,
        status="success",
        s3_url="https://example.test/final.jpg",
        is_final=True,
        cycle_id=cycle.id,
        attempt_number=1,
        sent_to_retake=True,
    )
    lock = MockExamLock(user_id=student.id, subject=SUBJECT, is_locked=True)
    db.add_all([work, lock])
    db.commit()

    client.cookies.set("session_id", session_factory(actor).id)
    return client, actor, student, work, lock


@pytest.mark.parametrize("make_unwritable", [_archive, _soft_delete], ids=["archived", "deleted"])
@pytest.mark.parametrize(
    "method,url,data,expected", ROUTES, ids=[f"{m} {u}" for m, u, _, _ in ROUTES]
)
def test_staff_cannot_write_to_archived_or_deleted_student(
    setup, db, make_unwritable, method, url, data, expected
):
    client, actor, student, work, lock = setup
    work_id, lock_id = work.id, lock.id
    make_unwritable(db, student.id, actor.id)

    path = url.format(sid=student.id, wid=work_id)
    form = {k: v.format(sid=student.id) for k, v in (data or {}).items()}
    if method == "delete":
        resp = client.delete(path, follow_redirects=False)
    else:
        resp = client.post(path, data=form, follow_redirects=False)

    assert resp.status_code == expected, resp.text

    db.expire_all()
    kept = db.get(Work, work_id)
    assert kept is not None
    assert kept.score is None
    assert kept.subject == SUBJECT
    assert kept.needs_revision is not True
    assert db.get(MockExamLock, lock_id).is_locked is True
