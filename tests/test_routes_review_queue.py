"""Очередь проверки ответов (владелец 31.08.2026).

Главный контракт — не «список красиво рисуется», а **куратор не видит чужих
учеников**. Соседний экран сдач домашних работ этой проверки не делает, и
любой куратор открывает там чужую работу по номеру в адресе; повторять эту
дыру нельзя.
"""

from app.models.task_block import (
    BLOCK_QUESTION,
    QUESTION_TEXT,
    TaskBlock,
    TaskBlockAnswer,
)
from app.models.tracker import TrackerTask
from app.services.task_blocks import save_response

REVIEW = "/cabinet/staff/review"


def _task_with_question(db, title="Материал"):
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


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return user


def test_curator_sees_only_own_students(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=610_001, name="Куратор", role_name="куратор")
    other_curator = user_factory(vk_id=610_002, name="Другой куратор", role_name="куратор")
    mine = user_factory(vk_id=610_003, name="Мой ученик")
    foreign = user_factory(vk_id=610_004, name="Чужой ученик")
    mine.curator_id = curator.id
    foreign.curator_id = other_curator.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, mine, "Ответ моего")
    _answer(db, task, block, foreign, "Ответ чужого")

    _login(client, session_factory, curator)
    page = client.get(REVIEW)

    assert page.status_code == 200
    assert "Мой ученик" in page.text
    assert "Чужой ученик" not in page.text
    assert "Ответ чужого" not in page.text


def test_head_teacher_sees_everyone(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=610_011, name="Куратор", role_name="куратор")
    head = user_factory(vk_id=610_012, name="Главный", is_admin=True, role_name="админ")
    student = user_factory(vk_id=610_013, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Мой ответ")

    _login(client, session_factory, head)
    page = client.get(REVIEW)

    assert page.status_code == 200
    assert "Ученик" in page.text
    assert "Мой ответ" in page.text


def test_marking_reviewed_removes_it_from_the_queue(client, db, user_factory, session_factory):
    head = user_factory(vk_id=610_021, name="Главный", is_admin=True, role_name="админ")
    student = user_factory(vk_id=610_022, name="Ученик")
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Мой ответ")
    answer = db.query(TaskBlockAnswer).one()

    _login(client, session_factory, head)
    resp = client.post(f"{REVIEW}/{answer.id}", json={"reviewed": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reviewed"] is True

    assert "Мой ответ" not in client.get(REVIEW).text
    # Во «всех» ответ остаётся — из очереди он ушёл, а не пропал.
    assert "Мой ответ" in client.get(f"{REVIEW}?only=all").text


def test_review_mark_can_be_taken_back(client, db, user_factory, session_factory):
    """Ткнули случайно — надо уметь вернуть (владелец 31.08.2026)."""
    head = user_factory(vk_id=610_031, name="Главный", is_admin=True, role_name="админ")
    student = user_factory(vk_id=610_032, name="Ученик")
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Мой ответ")
    answer = db.query(TaskBlockAnswer).one()

    _login(client, session_factory, head)
    client.post(f"{REVIEW}/{answer.id}", json={"reviewed": True})
    back = client.post(f"{REVIEW}/{answer.id}", json={"reviewed": False})

    assert back.status_code == 200
    assert back.json()["reviewed"] is False
    assert "Мой ответ" in client.get(REVIEW).text


def test_curator_cannot_mark_a_foreign_answer(client, db, user_factory, session_factory):
    """Подстановка чужого номера в адрес не должна проходить."""
    curator = user_factory(vk_id=610_041, name="Куратор", role_name="куратор")
    other = user_factory(vk_id=610_042, name="Другой куратор", role_name="куратор")
    foreign = user_factory(vk_id=610_043, name="Чужой ученик")
    foreign.curator_id = other.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, foreign, "Ответ чужого")
    answer = db.query(TaskBlockAnswer).one()

    _login(client, session_factory, curator)
    resp = client.post(f"{REVIEW}/{answer.id}", json={"reviewed": True})

    assert resp.status_code == 404
    db.expire_all()
    assert db.get(TaskBlockAnswer, answer.id).reviewed_at is None


def test_student_cannot_open_the_queue(client, db, user_factory, session_factory):
    student = user_factory(vk_id=610_051, name="Ученик")
    _login(client, session_factory, student)

    assert client.get(REVIEW).status_code in (403, 404)


def test_filter_by_student(client, db, user_factory, session_factory):
    head = user_factory(vk_id=610_061, name="Главный", is_admin=True, role_name="админ")
    first = user_factory(vk_id=610_062, name="Аня")
    second = user_factory(vk_id=610_063, name="Борис")
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, first, "Ответ Ани")
    _answer(db, task, block, second, "Ответ Бориса")

    _login(client, session_factory, head)
    page = client.get(f"{REVIEW}?student={first.id}")

    assert "Ответ Ани" in page.text
    assert "Ответ Бориса" not in page.text
