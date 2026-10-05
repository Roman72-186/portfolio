"""Проверка по ученику: вкладка «Задания» карточки, счётчик в списке и адреса
действий.

До 05.10.2026 это был отдельный экран `/cabinet/staff/students-review`
(`plans/2026-09-01-apparchi-student-centric-review.md`); владелец перенёс
проверку в карточку «Учеников» (`docs/invariants/cabinet.md`, «Проверка
ученика — во вкладке «Задания»»). Адреса действий остались прежними. Тесты на
ответы блоков заданий (`task-block/.../reviewed`) перенесены сюда со сноса
экрана `/cabinet/staff/review` 02.09.2026 — контракт «куратор не видит и не
трогает чужих учеников» тот же.
"""
from datetime import date, datetime, timezone

from app.models.exam_cycle import ExamCycle
from app.models.task_block import BLOCK_QUESTION, QUESTION_TEXT, TaskBlock, TaskBlockAnswer
from app.models.tracker import TrackerTask
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.review_aggregate import unreviewed_counts_by_student
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


def _row(html, student_id):
    return html.split(f'id="srow-{student_id}"', 1)[1].split("</button>", 1)[0]


def _badge_tag(row):
    return row.split("data-pending-badge", 1)[1].split(">", 1)[0]


def test_students_list_counts_unreviewed_for_curator(auth_client, db, user_factory, session_factory):
    """Очередь «кого проверять» — счётчик в списке «Учеников», и куратору
    тоже. Пробник первой версии в счёт не идёт с 06.10.2026 — его вкладки в
    карточке нет."""
    curator = user_factory(vk_id=860_101, name="Куратор", role_name="куратор")
    _, student = auth_client
    student.curator_id = curator.id
    db.commit()
    _work(db, student.id, score=None)
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Свет и тень")

    client, _ = auth_client
    client.cookies.set("session_id", session_factory(curator).id)
    html = client.get("/cabinet/students").text

    row = _row(html, student.id)
    assert 'data-unreviewed="1"' in row
    assert "hidden" not in _badge_tag(row)
    assert 'id="filter-unreviewed"' in html


def test_students_list_hides_zero_counter(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_102, name="Куратор своя", role_name="куратор")
    student = user_factory(vk_id=860_103, name="Свой ученик")
    student.curator_id = curator.id
    db.commit()

    client.cookies.set("session_id", session_factory(curator).id)
    row = _row(client.get("/cabinet/students").text, student.id)

    assert 'data-unreviewed="0"' in row
    assert "hidden" in _badge_tag(row)


def test_old_review_addresses_lead_to_student_card(db, user_factory, session_factory, client):
    """Экран снят 05.10.2026; на его адреса ведут уведомления и закладки."""
    curator = user_factory(vk_id=860_104, name="Куратор", role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)

    listing = client.get("/cabinet/staff/students-review", follow_redirects=False)
    detail = client.get("/cabinet/staff/students-review/42?week=2026-09-03", follow_redirects=False)

    assert listing.status_code == 302
    assert listing.headers["location"] == "/cabinet/students"
    assert detail.status_code == 302
    assert detail.headers["location"] == "/cabinet/students?student=42&tab=tasks"


def test_review_menu_item_is_gone(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=860_105, name="Куратор", role_name="куратор")
    chief = user_factory(vk_id=860_106, name="Главный", role_name="админ")
    for staff in (curator, chief):
        client.cookies.set("session_id", session_factory(staff).id)
        html = client.get("/cabinet/students").text
        assert 'href="/cabinet/staff/students-review"' not in html
        assert "Проверка по ученику" not in html


def test_tasks_tab_buttons_for_curator(db, user_factory, session_factory, client):
    """Куратор отмечает «просмотрено», но не возвращает и не ставит балл."""
    curator = user_factory(vk_id=860_107, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_108, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Свет и тень")

    client.cookies.set("session_id", session_factory(curator).id)
    data = client.get(f"/cabinet/students/{student.id}/tasks").json()

    assert data["can_review"] is True
    assert data["can_send_revision"] is False
    assert data["can_score"] is False
    item = data["items"][0]
    assert item["question"] == "Как прошло?"
    assert item["text"] == "Свет и тень"
    assert item["is_reviewed"] is False
    assert item["submitted_label"]


def test_tasks_tab_buttons_for_chief(db, user_factory, session_factory, client):
    chief = user_factory(vk_id=860_116, name="Главный", role_name="админ")
    student = user_factory(vk_id=860_117, name="Ученик")
    client.cookies.set("session_id", session_factory(chief).id)

    data = client.get(f"/cabinet/students/{student.id}/tasks").json()

    assert data["can_review"] is True
    assert data["can_send_revision"] is True
    assert data["can_score"] is True


def test_moderator_tasks_tab_has_no_buttons(db, user_factory, session_factory, client):
    """Модератор — наблюдатель: рангу 4 возврат формально положен, но раздела
    проверки у него нет, и кнопок тоже нет — сервер ответил бы 403."""
    moderator = user_factory(vk_id=860_118, name="Модератор", role_name="модератор")
    student = user_factory(vk_id=860_119, name="Ученик")
    client.cookies.set("session_id", session_factory(moderator).id)

    data = client.get(f"/cabinet/students/{student.id}/tasks").json()

    assert data["can_review"] is False
    assert data["can_send_revision"] is False


def test_mock_is_not_counted_in_card_or_list(db, user_factory, session_factory, client):
    """Пробник первой версии не входит ни в «Учёбу сейчас», ни в счётчик списка
    (06.10.2026): вкладки «Пробники» в карточке нет, открыть его оттуда негде."""
    chief = user_factory(vk_id=860_122, name="Главный", role_name="админ")
    student = user_factory(vk_id=860_123, name="Ученик")
    _work(db, student.id, score=None)
    client.cookies.set("session_id", session_factory(chief).id)

    def study_now():
        return client.get(f"/cabinet/students/{student.id}/profile").json()["student"]["study_now"]

    first = study_now()
    assert first["unreviewed"] == 0
    assert "review_tab" not in first
    assert unreviewed_counts_by_student(db, curator_id=None, role_rank=5).get(student.id) is None
    task, block = _task_with_question(db)
    db.commit()
    _answer(db, task, block, student, "Ответ")
    assert study_now()["unreviewed"] == 1
    assert unreviewed_counts_by_student(db, curator_id=None, role_rank=5)[student.id] == 1


def test_chief_returns_mock_and_nobody_reviews_until_resubmission(
    db, user_factory, session_factory, client,
):
    """Свой куратор пробник не возвращает (только ГП, 04.10.2026); после
    возврата ГП отметить «просмотрено» нельзя до новой сдачи."""
    curator = user_factory(vk_id=860_140, name="Куратор", role_name="куратор")
    chief = user_factory(vk_id=860_146, name="Главный", role_name="админ")
    student = user_factory(vk_id=860_141, name="Ученик")
    student.curator_id = curator.id
    db.commit()
    work = _work(db, student.id, score=None)
    work.viewed_at = datetime.now(timezone.utc)
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)
    refused = client.post(f"/cabinet/students/{student.id}/mock-exams/{work.id}/revision")
    assert refused.status_code == 403
    client.cookies.set("session_id", session_factory(chief).id)

    returned = client.post(f"/cabinet/students/{student.id}/mock-exams/{work.id}/revision")

    assert returned.status_code == 200
    db.refresh(work)
    assert work.needs_revision is True
    assert work.viewed_at is None
    assert client.post(f"/cabinet/staff/students-review/work/{work.id}/viewed").status_code == 409


def test_curator_cannot_return_foreign_mock(db, user_factory, session_factory, client):
    owner = user_factory(vk_id=860_142, name="Свой куратор", role_name="куратор")
    other = user_factory(vk_id=860_143, name="Чужой куратор", role_name="куратор")
    student = user_factory(vk_id=860_144, name="Ученик")
    student.curator_id = owner.id
    db.commit()
    work = _work(db, student.id)
    client.cookies.set("session_id", session_factory(other).id)

    response = client.post(f"/cabinet/students/{student.id}/mock-exams/{work.id}/revision")

    assert response.status_code == 403
    db.refresh(work)
    assert work.needs_revision is False


def test_curator_cannot_open_foreign_student_tasks(db, user_factory, session_factory, client):
    owner = user_factory(vk_id=860_150, name="Куратор своя", role_name="куратор")
    other = user_factory(vk_id=860_151, name="Куратор чужая", role_name="куратор")
    student = user_factory(vk_id=860_109, name="Ученик")
    student.curator_id = owner.id
    db.commit()

    client.cookies.set("session_id", session_factory(other).id)
    resp = client.get(f"/cabinet/students/{student.id}/tasks")

    assert resp.status_code in (403, 404)


def test_moderator_cannot_open_student_review(db, user_factory, session_factory, client):
    """Модератор — наблюдатель (решение владельца 28.09.2026): «Проверка по ученику» ему закрыта,
    учеников он смотрит в разделе «Ученики»."""
    moderator = user_factory(vk_id=860_112, name="Модератор", role_name="модератор")
    student = user_factory(vk_id=860_113, name="Ученик")  # curator_id остаётся None

    client.cookies.set("session_id", session_factory(moderator).id)
    resp = client.get(f"/cabinet/staff/students-review/{student.id}")

    assert resp.status_code == 403


def test_moderator_cannot_mark_work_viewed(db, user_factory, session_factory, client):
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


def test_task_without_due_date_is_in_the_card(db, user_factory, session_factory, client):
    """Регрессия 02.09.2026: фильтр недели шёл по `TrackerTask.due_at`, и ответ
    на задание без дедлайна не находился ни в одной неделе. Недель в карточке
    нет, но ответ без дедлайна по-прежнему должен быть виден."""
    curator = user_factory(vk_id=860_132, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=860_133, name="Ученик")
    student.curator_id = curator.id
    task, block = _task_with_question(db)
    assert task.due_at is None
    db.commit()
    _answer(db, task, block, student, "Ответ без дедлайна")

    client.cookies.set("session_id", session_factory(curator).id)
    items = client.get(f"/cabinet/students/{student.id}/tasks").json()["items"]

    assert [i["text"] for i in items] == ["Ответ без дедлайна"]


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


# ── точка А сюда больше не входит ───────────────────────────────────────────


def _before_work(db, user_id):
    work = Work(
        user_id=user_id, work_type="before", month="сентябрь", year=2026,
        filename="before.jpg", s3_url="https://s3.example.com/before.jpg",
        status="success",
    )
    db.add(work)
    db.commit()
    return work


def test_point_a_is_not_in_tasks_tab(
    client, db, user_factory, session_factory
):
    """Карточка «Портфолио «До» — точка А» снята отсюда 15.09.2026.

    До этого балл за работы «До» ставился прямо в недельной ленте проверки.
    Теперь входная оценка целиком живёт на своём экране
    `/cabinet/staff/point-a` рядом с пробниками и контрольными, и две точки
    простановки одного балла были бы дублем. Тест держит границу: вернётся
    карточка сюда — вернётся и расхождение.
    """
    student = user_factory(vk_id=870_001, name="Ученик Точкин")
    admin = user_factory(vk_id=870_002, name="Главный преподаватель", role_name="админ")
    _before_work(db, student.id)
    client.cookies.set("session_id", session_factory(admin).id)

    resp = client.get(f"/cabinet/students/{student.id}/tasks")

    assert resp.status_code == 200
    assert resp.json()["items"] == []
