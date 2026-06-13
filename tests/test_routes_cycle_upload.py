"""Тесты нового flow цикла Пробника: финальное фото + до 10 этапных.

Эндпоинты:
  POST /upload/probnik/final         — ровно одно финальное фото (создаёт цикл + lock)
  POST /upload/probnik/intermediate  — до 10 этапных на финальную (parent_work_id)
"""
from datetime import date, timedelta
from unittest.mock import patch

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


def _intermediate(client, subject="Рисунок", n=1):
    files = [("photos", (f"stage{i}.jpg", _JPG_BYTES, "image/jpeg")) for i in range(n)]
    return client.post("/upload/probnik/intermediate",
                       data={"subject": subject}, files=files)


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


def test_probnik_final_s3_configured_failure_does_not_create_work(auth_client, db):
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    with (
        patch("app.api.cycle_upload.s3_service.is_configured", return_value=True),
        patch("app.api.cycle_upload.s3_service.upload_to_s3", return_value=None),
    ):
        resp = _final(client, "Рисунок")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["created"] == 0
    assert body["failed"] == 1
    assert body["error"] == "Ошибка S3"
    assert db.query(Work).filter(Work.user_id == user.id).count() == 0


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
    """Новый порядок: этапные загружаются первыми, финальное вторым.
    После финального back-fill проставляет parent_work_id этапным."""
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    # 1) Этапные первыми
    resp_interm = _intermediate(client, "Рисунок", n=3)
    assert resp_interm.status_code == 200
    assert resp_interm.json()["success"] is True
    assert resp_interm.json()["created"] == 3

    # 2) Финальное вторым
    resp_final = _final(client, "Рисунок")
    assert resp_final.status_code == 200
    final_id = resp_final.json()["work_ids"][0]

    # Back-fill: все три этапных должны указывать на финальное
    db.expire_all()
    stages = db.query(Work).filter(
        Work.parent_work_id == final_id, Work.is_final == False  # noqa: E712
    ).all()
    assert len(stages) == 3
    assert all(s.cycle_id is not None for s in stages)


def test_probnik_intermediate_caps_at_ten(auth_client, db):
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    # 10 ок (без финального — этапные загружаются первыми)
    assert _intermediate(client, "Рисунок", n=10).json()["created"] == 10
    # 11-я сверх лимита — отклонена
    resp = _intermediate(client, "Рисунок", n=1)
    assert resp.status_code == 422

    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first()
    stages = db.query(Work).filter(
        Work.cycle_id == cycle.id, Work.is_final == False  # noqa: E712
    ).count()
    assert stages == 10


def test_probnik_intermediate_requires_active_ticket(auth_client, db):
    """Без активного билета по предмету загрузка этапных отклоняется 404."""
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    # билет не создан — нет активного задания

    resp = _intermediate(client, "Рисунок", n=1)
    assert resp.status_code == 404
    assert resp.json()["success"] is False


# ── Закрытие цикла снимает блокировку ────────────────────────────────────────

def test_closing_cycle_releases_lock(auth_client, db):
    """Ручное закрытие цикла (после выставления балла) снимает MockExamLock."""
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle
    from app.models.mock_exam_lock import MockExamLock
    from app.services.exam_cycle import close_cycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]

    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 80
    db.commit()

    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first()
    assert close_cycle(db, cycle) is True
    db.commit()

    db.refresh(cycle)
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
    from app.services.exam_cycle import close_cycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]
    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 80
    db.commit()
    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first()
    close_cycle(db, cycle)
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
    from app.services.exam_cycle import close_cycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    ticket1 = _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]
    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 80
    db.commit()
    cycle1 = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first()
    close_cycle(db, cycle1)
    db.commit()
    cycle1_id = cycle1.id

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


# ── «На доработку» (revision) не снимает реальную блокировку пересдачи ───────

def test_revision_reopens_submission_for_same_ticket(
    client, session_factory, regular_user, admin_user, db
):
    """Суперадмин шлёт пробник «на доработку» (POST .../mock-exams/{id}/revision).

    UI обещает студенту («Вернуть пробник на доработку? Студент сможет
    загрузить новые фото пробника.») и шлёт уведомление «Загрузи новые фото
    выполненного задания».

    revision выставляет Work.needs_revision=True на финале — has_submitted_for_ticket
    перестаёт считать его сдачей по текущему билету, и повторная загрузка
    проходит через _overwrite_final: то же Work.id, тот же cycle_id и
    attempt_number, score/comment/needs_revision сброшены, диалог ОС не теряется.
    После перезаписи лок снова взводится (is_locked=True) — следующая
    пересдача без новой revision опять даёт 409.
    """
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle
    from app.models.mock_exam_lock import MockExamLock

    student_sess = session_factory(regular_user)
    admin_sess = session_factory(admin_user)

    client.cookies.set("session_id", student_sess.id)
    _create_active_period(db, regular_user, "mock_exam")
    _create_active_ticket(db, regular_user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]

    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/students/{regular_user.id}/mock-exams/{work_id}/revision")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # MockExamLock разблокирован...
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id, MockExamLock.subject == "Рисунок"
    ).first()
    assert lock.is_locked is False

    # ...цикл остался открытым и привязанным к тому же билету (балл не ставился)
    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == regular_user.id).first()
    assert cycle.closed_at is None
    db.expire_all()
    final = db.query(Work).filter(
        Work.id == work_id, Work.work_type == WORK_TYPE_MOCK_EXAM, Work.is_final == True  # noqa: E712
    ).first()
    assert final.cycle_id == cycle.id
    assert final.needs_revision is True

    # ...и реальный гейт (has_submitted_for_ticket) теперь пропускает пересдачу.
    client.cookies.set("session_id", student_sess.id)
    resp2 = _final(client, "Рисунок",
                    photos=[("photos", ("redo.jpg", _JPG_BYTES, "image/jpeg"))])
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["success"] is True
    # In-place перезапись: тот же Work.id, тот же цикл — диалог ОС не теряется.
    assert body2["work_ids"] == [work_id]
    assert body2["cycle_id"] == cycle.id

    db.expire_all()
    final = db.query(Work).filter(Work.id == work_id).first()
    assert final.needs_revision is False
    assert final.score is None
    assert final.filename == "redo.jpg"

    # Лок снова взведён — следующая попытка без revision снова 409.
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id, MockExamLock.subject == "Рисунок"
    ).first()
    assert lock.is_locked is True

    resp3 = _final(client, "Рисунок")
    assert resp3.status_code == 409
    assert resp3.json()["error"] == "работа сдана, ждите ОС"


def test_revision_rejected_for_closed_scored_cycle(
    client, session_factory, regular_user, admin_user, db
):
    """«На доработку» недоступна, если цикл уже закрыт с оценкой
    (close_cycle отработал) — иначе revision могла бы «развязать»
    уже завершённый цикл и рассинхронизировать его с выставленным баллом."""
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle
    from app.models.mock_exam_lock import MockExamLock
    from app.services.exam_cycle import close_cycle

    student_sess = session_factory(regular_user)
    admin_sess = session_factory(admin_user)

    client.cookies.set("session_id", student_sess.id)
    _create_active_period(db, regular_user, "mock_exam")
    _create_active_ticket(db, regular_user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]
    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 80
    db.commit()
    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == regular_user.id).first()
    close_cycle(db, cycle)
    db.commit()

    assert cycle.closed_at is not None

    client.cookies.set("session_id", admin_sess.id)
    resp = client.post(f"/cabinet/students/{regular_user.id}/mock-exams/{work_id}/revision")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Цикл уже закрыт с оценкой — отправка на доработку недоступна"

    db.expire_all()
    final = db.query(Work).filter(Work.id == work_id).first()
    assert final.needs_revision is False

    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == regular_user.id, MockExamLock.subject == "Рисунок"
    ).first()
    assert lock.is_locked is False  # снят close_cycle, revision его не трогала


def test_closed_cycle_final_appears_in_portfolio(auth_client, db):
    """Финалка закрытого цикла видна в разделе «Пробные экзамены» портфолио."""
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle
    from app.services.exam_cycle import close_cycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    work_id = _final(client, "Рисунок").json()["work_ids"][0]
    work = db.query(Work).filter(Work.id == work_id).first()
    work.score = 75
    db.commit()
    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first()
    close_cycle(db, cycle)
    db.commit()

    resp = client.get("/cabinet/portfolio")
    assert resp.status_code == 200
    # Секция «Пробные экзамены» — дневной календарь (партиал CYCCAL), данные
    # передаются в CYCCAL.init: финалка закрытого цикла видна с баллом.
    assert "portfolio-mock-root" in resp.text
    assert '"mock-portfolio"' in resp.text
    assert "final.jpg" in resp.text
