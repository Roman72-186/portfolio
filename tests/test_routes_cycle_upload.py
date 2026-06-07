"""Тесты нового flow цикла Пробника: финальное фото + до 10 этапных.

Эндпоинты:
  POST /upload/probnik/final         — ровно одно финальное фото (создаёт цикл + lock)
  POST /upload/probnik/intermediate  — до 10 этапных на финальную (parent_work_id)
"""
from datetime import date, timedelta

_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # minimal JPEG header


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


def _final(client, subject="Рисунок", photos=None):
    photos = photos or [("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))]
    return client.post("/upload/probnik/final", data={"subject": subject}, files=photos)


def _intermediate(client, parent_work_id, n=1):
    files = [("photos", (f"stage{i}.jpg", _JPG_BYTES, "image/jpeg")) for i in range(n)]
    return client.post("/upload/probnik/intermediate",
                       data={"parent_work_id": str(parent_work_id)}, files=files)


# ── Финальное фото ────────────────────────────────────────────────────────────

def test_probnik_final_creates_single_final_work_and_cycle(auth_client, db):
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle
    from app.models.mock_exam_lock import MockExamLock

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    resp = _final(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["created"] == 1
    assert body["attempt_number"] == 1
    assert len(body["work_ids"]) == 1

    works = db.query(Work).filter(Work.user_id == user.id).all()
    assert len(works) == 1
    assert works[0].work_type == WORK_TYPE_MOCK_EXAM
    assert works[0].is_final is True
    assert works[0].cycle_id is not None
    assert works[0].drive_status == "s3_only"

    # цикл и lock созданы
    assert db.query(ExamCycle).filter(ExamCycle.user_id == user.id).count() == 1
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == user.id, MockExamLock.subject == "Рисунок"
    ).first()
    assert lock is not None and lock.is_locked is True


def test_probnik_final_rejects_more_than_one_photo(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    two = [
        ("photos", ("a.jpg", _JPG_BYTES, "image/jpeg")),
        ("photos", ("b.jpg", _JPG_BYTES, "image/jpeg")),
    ]
    resp = _final(client, "Рисунок", photos=two)
    assert resp.status_code == 422
    assert resp.json()["success"] is False


def test_probnik_final_requires_active_ticket(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    # без билета
    resp = _final(client, "Рисунок")
    assert resp.status_code == 404
    assert resp.json()["success"] is False


def test_probnik_final_resubmit_blocked_in_open_cycle(auth_client, db):
    """Повторная сдача в открытом цикле блокируется 409 «работа сдана, ждите ОС»:
    работа уже сдана и ждёт обратной связи — финал не перезаписывается."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    r1 = _final(client, "Рисунок",
                photos=[("photos", ("first.jpg", _JPG_BYTES, "image/jpeg"))])
    assert r1.json()["success"] is True
    first_id = r1.json()["work_ids"][0]

    r2 = _final(client, "Рисунок",
                photos=[("photos", ("second.jpg", _JPG_BYTES, "image/jpeg"))])
    assert r2.status_code == 409
    assert r2.json()["success"] is False
    assert r2.json()["error"] == "работа сдана, ждите ОС"

    finals = db.query(Work).filter(
        Work.user_id == user.id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.is_final == True,  # noqa: E712
    ).all()
    assert len(finals) == 1
    assert finals[0].id == first_id           # тот же Work, не перезаписан
    assert finals[0].filename == "first.jpg"  # исходным фото
    # цикл не размножился
    assert db.query(ExamCycle).filter(ExamCycle.user_id == user.id).count() == 1


# ── Этапные фото ──────────────────────────────────────────────────────────────

def test_probnik_intermediate_attaches_to_final(auth_client, db):
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    parent_id = _final(client, "Рисунок").json()["work_ids"][0]

    resp = _intermediate(client, parent_id, n=3)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["created"] == 3

    stages = db.query(Work).filter(
        Work.parent_work_id == parent_id, Work.is_final == False  # noqa: E712
    ).all()
    assert len(stages) == 3
    assert all(s.cycle_id is not None for s in stages)


def test_probnik_intermediate_caps_at_ten(auth_client, db):
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    parent_id = _final(client, "Рисунок").json()["work_ids"][0]

    # 10 ок
    assert _intermediate(client, parent_id, n=10).json()["created"] == 10
    # 11-я сверх лимита — отклонена
    resp = _intermediate(client, parent_id, n=1)
    assert resp.status_code == 422

    stages = db.query(Work).filter(
        Work.parent_work_id == parent_id, Work.is_final == False  # noqa: E712
    ).count()
    assert stages == 10


def test_probnik_intermediate_requires_valid_parent(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    resp = _intermediate(client, parent_work_id=999999, n=1)
    assert resp.status_code == 400
    assert resp.json()["success"] is False


# ── Закрытие цикла снимает блокировку ────────────────────────────────────────

def test_closing_cycle_releases_lock(auth_client, db):
    """Выставление балла финалке закрывает цикл И снимает MockExamLock."""
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle
    from app.models.mock_exam_lock import MockExamLock
    from app.services.exam_cycle import close_cycle_if_scored

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]

    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 80
    db.commit()

    assert close_cycle_if_scored(db, work) is True
    db.commit()

    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first()
    assert cycle.closed_at is not None

    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == user.id, MockExamLock.subject == "Рисунок"
    ).first()
    assert lock is not None
    assert lock.is_locked is False
    assert lock.unlocked_at is not None


def test_probnik_blocked_after_close_same_ticket(auth_client, db):
    """После закрытия цикла (балл проставлен) пробник по ТОМУ ЖЕ билету остаётся
    закрытым — повторная сдача 409. Открывается только следующим билетом."""
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle
    from app.services.exam_cycle import close_cycle_if_scored

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]
    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 80
    db.commit()
    close_cycle_if_scored(db, work)
    db.commit()

    # Цикл закрыт, билет тот же → сдача всё ещё закрыта.
    resp = _final(client, "Рисунок")
    assert resp.status_code == 409
    assert resp.json()["error"] == "работа сдана, ждите ОС"
    # Новый цикл не создан.
    assert db.query(ExamCycle).filter(ExamCycle.user_id == user.id).count() == 1


def test_probnik_reopens_with_new_ticket(auth_client, db):
    """Главный кейс задачи: новый билет открывает сдачу заново → новый цикл."""
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle
    from app.services.exam_cycle import close_cycle_if_scored

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    ticket1 = _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]
    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 80
    db.commit()
    close_cycle_if_scored(db, work)
    db.commit()
    cycle1_id = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first().id

    # Куратор/админ выдаёт НОВЫЙ билет по тому же предмету.
    ticket2 = _create_active_ticket(db, user, "Рисунок")
    assert ticket2.id != ticket1.id

    resp = _final(client, "Рисунок")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["cycle_created"] is True
    assert body["cycle_id"] != cycle1_id

    cycles = (
        db.query(ExamCycle).filter(ExamCycle.user_id == user.id)
        .order_by(ExamCycle.id).all()
    )
    assert len(cycles) == 2
    assert cycles[0].closed_at is not None      # старый остаётся закрытым
    assert cycles[1].ticket_id == ticket2.id    # новый цикл привязан к новому билету


def test_closed_cycle_final_appears_in_portfolio(auth_client, db):
    """Финалка закрытого цикла видна в разделе «Пробные экзамены» портфолио."""
    from app.models.work import Work
    from app.services.exam_cycle import close_cycle_if_scored

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]
    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 75
    db.commit()
    close_cycle_if_scored(db, work)
    db.commit()

    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    # Секция «Пробные экзамены» — дневной календарь (партиал CYCCAL), данные
    # передаются в CYCCAL.init: финалка закрытого цикла видна с баллом.
    assert "portfolio-mock-root" in resp.text
    assert '"mock-portfolio"' in resp.text
    assert "final.jpg" in resp.text
