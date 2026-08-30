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
    from app.models.exam_assignment import ExamAssignment, ExamTicket
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
    db.commit()
    return ticket


def _embed(client, subject="Рисунок"):
    return client.get("/upload/mock-exam/embed", params={"subject": subject})


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
    from datetime import datetime, timezone
    from app.models.exam_cycle import ExamCycle
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    _create_active_period(db, user)
    ticket = _create_active_ticket(db, user, "Рисунок")
    cycle = ExamCycle(user_id=user.id, subject="Рисунок", ticket_id=ticket.id,
                       started_at=datetime.now(timezone.utc))
    db.add(cycle)
    db.flush()
    db.add(Work(
        user_id=user.id, work_type=WORK_TYPE_MOCK_EXAM,
        month="январь", year=2026, filename="mock.jpg",
        s3_url="https://example.test/mock.jpg", subject="Рисунок",
        tariff=user.tariff, status="success", score=None,
        cycle_id=cycle.id, is_final=True, created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = _embed(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["locked"] is True
    assert body["locked_reason"] == "waiting"
