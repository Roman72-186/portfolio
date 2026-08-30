"""JSON-эндпоинт `/upload/mock-exam/embed` — состояние Пробника по одному
предмету для инлайн-карточки на АОП (partials/inline/mock_exam.html), по
аналогии с `/cabinet/videos/{id}/embed` у видео (решение владельца 30.08.2026).

Должен отдавать то же состояние, что и отдельная страница `/upload/mock-exam`
считает для одного subject (billet/attempt/stage_state), без выбора предмета.
"""
from datetime import date, timedelta


def _create_active_period(db, user, feature="mock_exam"):
    from app.models.feature_period import FeaturePeriod
    from app.services.feature_periods import invalidate_feature_cache
    today = date.today()
    db.add(FeaturePeriod(
        feature=feature,
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=30),
        is_active=True,
        created_by_id=user.id,
    ))
    db.commit()
    invalidate_feature_cache(feature)


def _create_active_ticket(db, user, subject="Рисунок"):
    """Билет + связанный `TrackerTask` — мини-опрос ключуется по `task_id`
    (владелец 30.08.2026, см. `app/models/task_quiz.py`), без него
    `_task_id_for_assignment` не найдёт задание."""
    from app.models.exam_assignment import ExamAssignment, ExamTicket
    from app.models.tracker import SOURCE_EXAM_ASSIGNMENT, ITEM_MOCK_EXAM
    from app.services.tracker import create_task
    today = date.today()
    assignment = ExamAssignment(
        title=f"Тест {subject}", subject=subject,
        created_by_id=user.id, status="published",
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=1,
        title=f"Билет {subject}",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=30),
        assign_to_all=True,
    )
    db.add(ticket)
    db.flush()
    create_task(
        db,
        title=f"Пробник по предмету «{subject}»",
        user_id=user.id,
        subject=subject,
        kind=ITEM_MOCK_EXAM,
        source_kind=SOURCE_EXAM_ASSIGNMENT,
        source_id=assignment.id,
    )
    db.commit()
    return ticket


def _task_for_ticket(db, ticket):
    from app.models.tracker import SOURCE_EXAM_ASSIGNMENT, TrackerTask
    return (
        db.query(TrackerTask)
        .filter(
            TrackerTask.source_kind == SOURCE_EXAM_ASSIGNMENT,
            TrackerTask.source_id == ticket.assignment_id,
        )
        .one()
    )


def _embed(client, subject="Рисунок"):
    return client.get("/upload/mock-exam/embed", params={"subject": subject})


def _submit_final(db, user, ticket, subject="Рисунок"):
    """Билет + сданный финал в открытом (не оценённом) цикле — тот же признак
    "locked": "waiting", что читает `_locked_mock_subjects`/`_submitted_assignment_id`.
    Возвращает созданный `ExamCycle`."""
    from datetime import datetime, timezone
    from app.models.exam_cycle import ExamCycle
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    cycle = ExamCycle(user_id=user.id, subject=subject, ticket_id=ticket.id,
                       started_at=datetime.now(timezone.utc))
    db.add(cycle)
    db.flush()
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="январь", year=2026, filename="mock.jpg",
        s3_url="https://example.test/mock.jpg", subject=subject,
        tariff=user.tariff, status="success", score=None,
        cycle_id=cycle.id, is_final=True, created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return cycle


def test_embed_rejects_invalid_subject(auth_client):
    client, _ = auth_client
    resp = _embed(client, "Не предмет")
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_subject"


def test_embed_reports_feature_closed_by_default(auth_client):
    """Без активного FeaturePeriod пробники закрыты по умолчанию
    (`is_feature_available`) — тот же гейт, что у отдельной страницы."""
    client, _ = auth_client
    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_available"] is False
    assert "feature_message" in body


def test_embed_no_ticket_reports_has_ticket_false(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user)
    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_available"] is True
    assert body["has_ticket"] is False
    assert body["attempt"] is None
    assert body["locked"] is False


def test_embed_active_ticket_without_attempt_allows_start(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user)
    _create_active_ticket(db, user, "Рисунок")

    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_ticket"] is True
    assert body["start_open"] is True
    assert body["attempt"] is None


def test_embed_after_start_returns_ticket_and_timer(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user)
    _create_active_ticket(db, user, "Рисунок")

    start_resp = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})
    assert start_resp.status_code == 200

    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["attempt"] is not None
    assert body["attempt"]["ticket_title"] == "Билет Рисунок"
    assert body["attempt"]["started_at"]
    assert body["attempt"]["expires_at"]
    assert body["stage_state"]["limit"] > 0


def test_embed_locks_subject_after_final_submitted(auth_client, db):
    """Открытый сданный цикл (финал загружен, ОС ещё не дана) блокирует
    предмет — та же проверка `_locked_mock_subjects`, что у отдельной
    страницы `/upload/mock-exam` (см. test_routes_upload.py, аналогичный
    тест там строит состояние напрямую через ORM, а не через реальную
    заливку в S3, которая недоступна в этом окружении)."""
    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    _submit_final(db, user, ticket, "Рисунок")

    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["locked"] is True
    assert body["locked_reason"] == "waiting"


# ── Мини-опрос после сдачи (владелец 30.08.2026) ─────────────────────────────

def test_embed_locked_without_quiz_questions_omits_quiz(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    _submit_final(db, user, ticket, "Рисунок")

    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["quiz_questions"] == []
    assert body["quiz_submit_endpoint"] is None


def test_embed_locked_with_quiz_questions_exposes_them_unanswered(auth_client, db):
    from app.models.task_quiz import TaskQuizQuestion

    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    task = _task_for_ticket(db, ticket)
    db.add(TaskQuizQuestion(task_id=task.id, text="Как прошла сдача?", sort_order=0))
    db.add(TaskQuizQuestion(task_id=task.id, text="Что было сложно?", sort_order=1))
    db.commit()
    _submit_final(db, user, ticket, "Рисунок")

    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["quiz_questions"] == ["Как прошла сдача?", "Что было сложно?"]
    assert body["quiz_answers"] is None
    assert body["quiz_submit_endpoint"] == "/upload/mock-exam/quiz"


def test_embed_unlocked_omits_quiz_even_if_configured(auth_client, db):
    """Мини-опрос виден только после сдачи финала — билет без активной
    попытки не должен его показывать заранее."""
    from app.models.task_quiz import TaskQuizQuestion

    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    task = _task_for_ticket(db, ticket)
    db.add(TaskQuizQuestion(task_id=task.id, text="Как прошла сдача?", sort_order=0))
    db.commit()

    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["locked"] is False
    assert body["quiz_questions"] == []


def test_submit_quiz_requires_submitted_final(auth_client, db):
    from app.models.task_quiz import TaskQuizQuestion

    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    task = _task_for_ticket(db, ticket)
    db.add(TaskQuizQuestion(task_id=task.id, text="Вопрос", sort_order=0))
    db.commit()

    resp = client.post("/upload/mock-exam/quiz", json={"subject": "Рисунок", "answers": ["ответ"]})
    assert resp.status_code == 409


def test_submit_quiz_requires_configured_questions(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    _submit_final(db, user, ticket, "Рисунок")

    resp = client.post("/upload/mock-exam/quiz", json={"subject": "Рисунок", "answers": ["ответ"]})
    assert resp.status_code == 404


def test_submit_quiz_rejects_answer_count_mismatch(auth_client, db):
    from app.models.task_quiz import TaskQuizQuestion

    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    task = _task_for_ticket(db, ticket)
    db.add(TaskQuizQuestion(task_id=task.id, text="Вопрос 1", sort_order=0))
    db.add(TaskQuizQuestion(task_id=task.id, text="Вопрос 2", sort_order=1))
    db.commit()
    _submit_final(db, user, ticket, "Рисунок")

    resp = client.post("/upload/mock-exam/quiz", json={"subject": "Рисунок", "answers": ["только один ответ"]})
    assert resp.status_code == 422


def test_submit_quiz_success_saves_answers_and_embed_returns_them(auth_client, db):
    from app.models.task_quiz import TaskQuizQuestion, TaskQuizResponse

    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    task = _task_for_ticket(db, ticket)
    db.add(TaskQuizQuestion(task_id=task.id, text="Как прошла сдача?", sort_order=0))
    db.add(TaskQuizQuestion(task_id=task.id, text="Что было сложно?", sort_order=1))
    db.commit()
    _submit_final(db, user, ticket, "Рисунок")

    resp = client.post(
        "/upload/mock-exam/quiz",
        json={"subject": "Рисунок", "answers": ["Нормально", "Свет"]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert db.query(TaskQuizResponse).filter(
        TaskQuizResponse.task_id == task.id,
        TaskQuizResponse.user_id == user.id,
    ).count() == 1

    # Повторный embed отдаёт уже сохранённые ответы, не пустую форму.
    resp = _embed(client, "Рисунок")
    body = resp.json()
    assert body["quiz_answers"] == ["Нормально", "Свет"]
