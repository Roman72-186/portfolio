"""Единый экран проверки: список учеников + карточка ученика по всем доменам.

`plans/2026-09-01-apparchi-student-centric-review.md`, этап 5. Тесты на
ответы блоков заданий (`task-block/.../reviewed`) перенесены сюда со сноса
отдельного экрана `/cabinet/staff/review` 02.09.2026 — контракт («куратор не
видит и не трогает чужих учеников») тот же, что был у `test_routes_review_queue.py`.
"""
from datetime import date, datetime, timezone

from app.models.exam_cycle import ExamCycle
from app.models.task_block import BLOCK_QUESTION, QUESTION_TEXT, TaskBlock, TaskBlockAnswer
from app.models.tracker import TrackerTask
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.task_blocks import save_response


def _task_with_question(db, title="Материал"):
    # Без due_at нарочно: у части заданий дедлайна нет вовсе, а неделя на
    # этом экране считается по дате сдачи ответа, не по дедлайну (регрессия
    # найдена и починена 02.09.2026, см. test_task_without_due_date_still_visible_this_week).
    task = TrackerTask(title=title, kind="material", is_published=True, assign_to_all=True)
    db.add(task)
    db.flush()
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, question_type=QUESTION_TEXT,
        body="Как прошло?", sort_order=0,
    )
    db.add(block)
    db.flush()
    return task, block


def _answer(db, task, block, student, text):
    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"text": text}},
    )
    db.commit()


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


def test_moderator_cannot_open_unassigned_student_review(db, user_factory, session_factory, client):
    """Advisor-ревью 01.09.2026: require_curator пропускает и ранг 3, без явной
    проверки в _check_student_access модератор видел бы любого ученика."""
    moderator = user_factory(vk_id=860_112, name="Модератор", role_name="модератор")
    student = user_factory(vk_id=860_113, name="Ученик")  # curator_id остаётся None

    client.cookies.set("session_id", session_factory(moderator).id)
    resp = client.get(f"/cabinet/staff/students-review/{student.id}")

    assert resp.status_code == 403


def test_moderator_cannot_mark_unassigned_work_viewed(db, user_factory, session_factory, client):
    moderator = user_factory(vk_id=860_114, name="Модератор", role_name="модератор")
    student = user_factory(vk_id=860_115, name="Ученик")
    work = _work(db, student.id, score=None)

    client.cookies.set("session_id", session_factory(moderator).id)
    resp = client.post(f"/cabinet/staff/students-review/work/{work.id}/viewed")

    assert resp.status_code == 403
    db.refresh(work)
    assert work.viewed_at is None


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


def test_task_without_due_date_still_visible_this_week(db, user_factory, session_factory, client):
    """Регрессия 02.09.2026: фильтр недели раньше шёл по `TrackerTask.due_at`,
    а не по дате сдачи ответа. У задания без дедлайна due_at — NULL, и
    `NULL >= week_start` в SQL ложно, поэтому ответ не находился ни в одной
    неделе. Решение владельца 01.09.2026 (вопрос 3) требовало фильтр по дате
    сдачи/создания записи — починено в `task_blocks.py::review_queue`."""
    curator = user_factory(vk_id=860_132, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_133, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    assert task.due_at is None
    db.commit()
    _answer(db, task, block, student, "Ответ без дедлайна")

    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.get(f"/cabinet/staff/students-review/{student.id}")

    assert resp.status_code == 200
    assert "Ответ без дедлайна" in resp.text


def test_detail_page_shows_task_block_question_and_answer(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_120, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_121, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Свет и тень")

    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.get(f"/cabinet/staff/students-review/{student.id}")

    assert resp.status_code == 200
    assert "Как прошло?" in resp.text
    assert "Свет и тень" in resp.text


def test_curator_can_toggle_task_block_answer_reviewed(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_122, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_123, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Ответ")
    answer = db.query(TaskBlockAnswer).one()

    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.post(f"/cabinet/staff/students-review/task-block/{answer.id}/reviewed", json={"reviewed": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["reviewed"] is True
    db.expire_all()
    assert db.get(TaskBlockAnswer, answer.id).reviewed_at is not None


def test_task_block_toggle_can_be_taken_back(db, user_factory, session_factory, client):
    """Ткнули случайно — надо уметь вернуть (владелец 31.08.2026)."""
    curator = user_factory(vk_id=860_124, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_125, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Ответ")
    answer = db.query(TaskBlockAnswer).one()

    client.cookies.set("session_id", session_factory(curator).id)
    client.post(f"/cabinet/staff/students-review/task-block/{answer.id}/reviewed", json={"reviewed": True})
    back = client.post(f"/cabinet/staff/students-review/task-block/{answer.id}/reviewed", json={"reviewed": False})

    assert back.status_code == 200
    assert back.json()["reviewed"] is False
    db.expire_all()
    assert db.get(TaskBlockAnswer, answer.id).reviewed_at is None


def test_curator_cannot_toggle_foreign_task_block_answer(db, user_factory, session_factory, client):
    """Подстановка чужого номера ответа в адрес не должна проходить."""
    owner = user_factory(vk_id=860_126, name="Куратор своя", role_name="куратор")
    other = user_factory(vk_id=860_127, name="Куратор чужая", role_name="куратор")
    foreign = user_factory(vk_id=860_128, name="Чужой ученик")
    foreign.curator_id = owner.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, foreign, "Ответ чужого")
    answer = db.query(TaskBlockAnswer).one()

    client.cookies.set("session_id", session_factory(other).id)
    resp = client.post(f"/cabinet/staff/students-review/task-block/{answer.id}/reviewed", json={"reviewed": True})

    assert resp.status_code == 403
    db.expire_all()
    assert db.get(TaskBlockAnswer, answer.id).reviewed_at is None


def test_head_teacher_can_toggle_any_task_block_answer(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_129, name="Куратор", role_name="куратор")
    head = user_factory(vk_id=860_130, name="Главный", is_admin=True, role_name="админ")
    student = user_factory(vk_id=860_131, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Ответ")
    answer = db.query(TaskBlockAnswer).one()

    client.cookies.set("session_id", session_factory(head).id)
    resp = client.post(f"/cabinet/staff/students-review/task-block/{answer.id}/reviewed", json={"reviewed": True})

    assert resp.status_code == 200


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
