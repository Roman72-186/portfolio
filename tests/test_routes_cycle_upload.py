"""Тесты нового flow цикла Пробника: финальное фото + до 10 этапных.

Эндпоинты:
  POST /upload/probnik/final         — ровно одно финальное фото (создаёт цикл + lock)
  POST /upload/probnik/intermediate  — до 10 этапных на финальную (parent_work_id)
"""
from datetime import date, datetime, timedelta, timezone
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


def _create_active_ticket(db, user, subject="Рисунок", *, opens_at=None, closes_at=None):
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
        opens_at=opens_at,
        closes_at=closes_at,
        assign_to_all=True,
    )
    db.add(ticket)
    db.commit()
    return ticket


def _create_active_ticket_set(db, user, subject="Рисунок", count=2):
    from app.models.exam_assignment import ExamAssignment, ExamTicket
    today = date.today()
    assignment = ExamAssignment(
        title=f"Тест {subject}", subject=subject,
        created_by_id=user.id, status="published",
    )
    db.add(assignment)
    db.flush()
    tickets = []
    for n in range(1, count + 1):
        ticket = ExamTicket(
            assignment_id=assignment.id, ticket_number=n,
            title=f"Билет {n}",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30),
            assign_to_all=True,
        )
        db.add(ticket)
        tickets.append(ticket)
    db.commit()
    return tickets


def _start(client, subject="Рисунок"):
    return client.post("/upload/mock-exam/start", data={"subject": subject})


def _final(client, subject="Рисунок", photos=None):
    photos = photos or [("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))]
    _start(client, subject)
    return client.post("/upload/probnik/final", data={"subject": subject}, files=photos)


def _intermediate(client, subject="Рисунок", n=1):
    files = [("photos", (f"stage{i}.jpg", _JPG_BYTES, "image/jpeg")) for i in range(n)]
    _start(client, subject)
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
    assert body["verified"] is True
    assert body["final_work_id"] == body["work_ids"][0]
    assert body["existing"] == 0
    assert body["remaining"] == 10

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


def test_probnik_final_uses_randomly_started_ticket(auth_client, db):
    from app.models.exam_cycle import ExamCycle
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    tickets = _create_active_ticket_set(db, user, "Рисунок", count=2)
    chosen = tickets[0]
    deterministic_latest = tickets[1]
    assert chosen.id != deterministic_latest.id

    with patch("app.api.upload.random.choice", return_value=chosen):
        start_resp = _start(client, "Рисунок")
    assert start_resp.status_code == 200
    assert start_resp.json()["ticket"]["id"] == chosen.id

    resp = client.post(
        "/upload/probnik/final",
        data={"subject": "Рисунок"},
        files=[("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))],
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).one()
    assert cycle.ticket_id == chosen.id
    work = db.query(Work).filter(
        Work.user_id == user.id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.is_final == True,  # noqa: E712
    ).one()
    assert work.cycle_id == cycle.id


def test_probnik_final_closes_current_ticket_attempt_and_expires_stale_ones(auth_client, db):
    """Сдача финала закрывает (completed_at) MockExamAttempt ТЕКУЩЕГО билета, а
    открытые попытки ДРУГИХ (старых) билетов помечает expired_at — не "сдано".

    Регрессия инцидента user_id=134: blanket completed_at=now() для ЛЮБЫХ
    открытых попыток маскировал месячную "зависшую" попытку под архивный билет
    так, будто она была сдана вместе с текущим финалом.
    """
    from app.models.exam_assignment import ExamAssignment, ExamTicket
    from app.models.mock_exam_attempt import MockExamAttempt
    from datetime import date, timedelta as _td

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    ticket = _create_active_ticket(db, user, "Рисунок")

    # Архивный билет того же предмета — снимок месячной "зависшей" попытки.
    today = date.today()
    old_assignment = ExamAssignment(
        title="Старое задание", subject="Рисунок",
        created_by_id=user.id, status="archived",
    )
    db.add(old_assignment)
    db.flush()
    old_ticket = ExamTicket(
        assignment_id=old_assignment.id, ticket_number=3,
        title="Билет №3 (архивный)",
        start_date=today - _td(days=40),
        end_date=today - _td(days=30),
        assign_to_all=True,
    )
    db.add(old_ticket)
    db.flush()

    current_attempt = MockExamAttempt(
        user_id=user.id, subject="Рисунок",
        ticket_id=ticket.id, ticket_title=ticket.title,
    )
    stale_attempt = MockExamAttempt(
        user_id=user.id, subject="Рисунок",
        ticket_id=old_ticket.id, ticket_title=old_ticket.title,
    )
    db.add_all([current_attempt, stale_attempt])
    db.commit()
    current_id, stale_id = current_attempt.id, stale_attempt.id

    resp = _final(client, "Рисунок")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    db.expire_all()
    current = db.query(MockExamAttempt).filter(MockExamAttempt.id == current_id).first()
    stale = db.query(MockExamAttempt).filter(MockExamAttempt.id == stale_id).first()

    assert current.completed_at is not None
    assert current.expired_at is None

    assert stale.completed_at is None
    assert stale.expired_at is not None


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


def test_probnik_final_requires_started_attempt(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    resp = client.post(
        "/upload/probnik/final",
        data={"subject": "Рисунок"},
        files=[("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))],
    )

    assert resp.status_code == 403
    assert resp.json()["success"] is False
    assert "Начать пробник" in resp.json()["error"]


def test_probnik_final_accepts_submission_after_timer_elapsed(auth_client, db):
    """«Время на выполнение» визуальное: финал принимается спустя часы после «Начать»,
    пока сдача в пределах периода доступа билета.

    Раньше попытка протухала через 90 мин (is_mock_exam_attempt_open) и сдача
    давала 403 — это и есть баг «ученики не могут сдать в отведённый период».
    """
    from datetime import datetime, timezone, timedelta
    from app.models.mock_exam_attempt import MockExamAttempt

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")  # период доступа охватывает «сейчас»
    _start(client, "Рисунок")
    attempt = db.query(MockExamAttempt).filter(
        MockExamAttempt.user_id == user.id,
        MockExamAttempt.subject == "Рисунок",
    ).first()
    # Старт «давно» — далеко за пределами duration; таймер визуальный, сдачу не блокирует.
    attempt.started_at = datetime.now(timezone.utc) - timedelta(hours=6)
    db.commit()

    resp = client.post(
        "/upload/probnik/final",
        data={"subject": "Рисунок"},
        files=[("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))],
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    db.refresh(attempt)
    # Таймер не «протухил» попытку — она закрыта как сданная, а не expired.
    assert attempt.expired_at is None
    assert attempt.completed_at is not None


def test_probnik_final_blocked_after_window_closes_without_attempt(auth_client, db):
    """Билет, выданный куратором с уже истёкшим окном (opens_at..closes_at в прошлом),
    остаётся «активным» для сдачи (get_active_ticket больше не фильтрует по периоду —
    см. is_mock_exam_ticket_submission_open), но получить его («Начать пробник») всё
    ещё нельзя вне периода (is_mock_exam_ticket_start_open). Без открытой попытки
    сдача блокируется тем же 403 «Сначала нажмите «Начать пробник»»."""
    from app.services.tz import now_msk

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _now = now_msk()
    _create_active_ticket(
        db, user, "Рисунок",
        opens_at=_now - timedelta(days=20),
        closes_at=_now - timedelta(days=10),  # окно закрылось ~10 дней назад
    )

    resp = client.post(
        "/upload/probnik/final",
        data={"subject": "Рисунок"},
        files=[("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))],
    )

    assert resp.status_code == 403
    assert resp.json()["success"] is False


def test_probnik_final_allowed_after_window_closes_with_open_attempt(auth_client, db):
    """Сдача больше НЕ ограничена периодом доступа билета: если ученик успел нажать
    «Начать пробник» до closes_at, финал принимается даже после того, как период
    закрылся (см. is_mock_exam_ticket_submission_open — теперь всегда True). Раньше
    после closes_at билет «пропадал» из get_active_ticket и доделанная работа не
    принималась без возможности досдать."""
    from app.models.exam_assignment import ExamTicket as ExamTicketModel

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    ticket = _create_active_ticket(db, user, "Рисунок")  # период открыт на момент старта

    start_resp = _start(client, "Рисунок")
    assert start_resp.status_code == 200

    # Период доступа истёк, пока ученик ещё работал над заданием.
    ticket = db.query(ExamTicketModel).filter(ExamTicketModel.id == ticket.id).first()
    ticket.closes_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    resp = client.post(
        "/upload/probnik/final",
        data={"subject": "Рисунок"},
        files=[("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))],
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_probnik_final_resubmit_blocked_in_open_cycle(auth_client, db):
    """После сдачи финала перезалив запрещён даже в ОТКРЫТОМ цикле (по запросу
    владельца): ученик НЕ может перезагрузить работу по своей воле. Повторный POST →
    409 «работа сдана, ждите обратной связи». Финал остаётся прежним, цикл один."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    r1 = _final(client, "Рисунок",
                photos=[("photos", ("first.jpg", _JPG_BYTES, "image/jpeg"))])
    assert r1.json()["success"] is True
    first_id = r1.json()["work_ids"][0]

    # Админ ставит балл — но цикл ещё открыт. Перезалив всё равно запрещён.
    work = db.query(Work).filter(Work.id == first_id).first()
    work.score = 80
    db.commit()

    r2 = _final(client, "Рисунок",
                photos=[("photos", ("second.jpg", _JPG_BYTES, "image/jpeg"))])
    assert r2.status_code == 409
    assert r2.json()["error"] == "работа сдана, ждите обратной связи"

    db.expire_all()
    finals = db.query(Work).filter(
        Work.user_id == user.id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.is_final == True,  # noqa: E712
    ).all()
    assert len(finals) == 1
    assert finals[0].id == first_id            # финал не тронут
    assert finals[0].filename == "first.jpg"   # фото осталось прежним
    # цикл не размножился
    assert db.query(ExamCycle).filter(ExamCycle.user_id == user.id).count() == 1


def test_probnik_intermediate_blocked_after_final_submitted(auth_client, db):
    """Этапные тоже нельзя докидывать после сдачи финала: 409 (в нормальном потоке
    этапные грузятся ДО финала, поэтому первая сдача не страдает)."""
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    assert _final(client, "Рисунок").json()["success"] is True

    resp = _intermediate(client, "Рисунок", n=1)
    assert resp.status_code == 409
    assert resp.json()["error"] == "работа сдана, ждите обратной связи"


def test_probnik_redo_after_revision_notifies_scorer(auth_client, db):
    """Redo после «на доработку» (needs_revision=True) перезаписывает финал in-place и
    шлёт уведомление уже вовлечённому staff (scored_by_id) — балл молча сбрасывается,
    проверяющий должен узнать о новом фото. needs_revision снят redo-загрузкой."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.notification import Notification

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    first_id = _final(client, "Рисунок").json()["work_ids"][0]
    # Первая сдача — staff ещё не вовлечён → уведомлений нет.
    assert db.query(Notification).count() == 0

    # Админ (id=777) выставил балл и вернул работу «на доработку» (needs_revision).
    # has_submitted_for_ticket такой финал за сдачу не считает → redo-загрузка пройдёт.
    work = db.query(Work).filter(Work.id == first_id).first()
    work.score = 80
    work.scored_by_id = 777
    work.needs_revision = True
    db.commit()

    r2 = _final(client, "Рисунок",
                photos=[("photos", ("redo.jpg", _JPG_BYTES, "image/jpeg"))])
    assert r2.status_code == 200
    assert r2.json()["success"] is True
    assert r2.json()["work_ids"] == [first_id]  # in-place перезапись

    notes = db.query(Notification).filter(Notification.user_id == 777).all()
    assert len(notes) == 1
    assert notes[0].work_id == first_id
    # ученику уведомление о собственном redo не шлётся
    assert db.query(Notification).filter(Notification.user_id == user.id).count() == 0

    db.expire_all()
    final = db.query(Work).filter(
        Work.id == first_id, Work.work_type == WORK_TYPE_MOCK_EXAM
    ).first()
    assert final.needs_revision is False        # снят redo-загрузкой
    assert final.filename == "redo.jpg"
    assert final.score is None                  # балл сброшен


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


def test_probnik_ten_stage_and_final_refresh_near_expired_session(auth_client, db):
    """Real CSRF + auth contract for the browser's 10+1 upload flow.

    The usual test client disables ``require_csrf`` for concise route tests.
    Here it is deliberately restored: after a student has started the exam,
    the UI refreshes its token before uploading.  That request must also renew
    an almost-expired auth session before the 10 intermediate files and final
    work are accepted.
    """
    from app.dependencies import require_csrf
    from app.main import app
    from app.models.session import Session
    from app.config import settings
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    csrf_override = app.dependency_overrides.pop(require_csrf)
    try:
        _create_active_period(db, user, "mock_exam")
        _create_active_ticket(db, user, "Рисунок")

        def fresh_csrf_token() -> tuple[str, object]:
            response = client.get(
                "/upload/mock-exam/csrf",
                headers={"Accept": "application/json"},
            )
            assert response.status_code == 200
            return response.json()["csrf_token"], response

        start_token, _ = fresh_csrf_token()

        start = client.post(
            "/upload/mock-exam/start",
            data={"subject": "Рисунок", "csrf_token": start_token},
            headers={"Accept": "application/json"},
        )
        assert start.status_code == 200

        # TestClient keeps the fixture cookie and the refreshed response cookie
        # under different test domains.  They must still carry one session id.
        session_ids = {
            cookie.value for cookie in client.cookies.jar
            if cookie.name == "session_id"
        }
        assert len(session_ids) == 1
        session_id = session_ids.pop()
        session = db.query(Session).filter(Session.id == session_id).first()
        session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        db.commit()

        token, refresh_response = fresh_csrf_token()
        assert "session_id=" in refresh_response.headers.get("set-cookie", "")
        db.refresh(session)
        refreshed_at = session.expires_at
        if refreshed_at.tzinfo is None:
            refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
        assert refreshed_at > datetime.now(timezone.utc) + timedelta(
            hours=settings.session_ttl_hours, seconds=-5
        )

        stage_files = [
            ("photos", (f"stage{i}.jpg", _JPG_BYTES, "image/jpeg"))
            for i in range(10)
        ]
        stages = client.post(
            "/upload/probnik/intermediate",
            data={"subject": "Рисунок", "csrf_token": token},
            files=stage_files,
            headers={"Accept": "application/json"},
        )
        assert stages.status_code == 200
        assert stages.json()["created"] == 10
        assert stages.json()["remaining"] == 0

        final = client.post(
            "/upload/probnik/final",
            data={"subject": "Рисунок", "csrf_token": fresh_csrf_token()[0]},
            files=[("photos", ("final.jpg", _JPG_BYTES, "image/jpeg"))],
            headers={"Accept": "application/json"},
        )
        assert final.status_code == 200
        final_body = final.json()
        assert final_body["success"] is True
        assert final_body["verified"] is True
        assert final_body["created"] == 1
        assert len(final_body["work_ids"]) == 1
        works = db.query(Work).filter(
            Work.cycle_id == final_body["cycle_id"],
            Work.work_type == WORK_TYPE_MOCK_EXAM,
        ).all()
        assert len(works) == 11
        assert sum(not work.is_final for work in works) == 10
        assert sum(work.is_final for work in works) == 1
    finally:
        app.dependency_overrides[require_csrf] = csrf_override


def test_probnik_intermediate_reports_remaining_slots(auth_client, db):
    from app.models.work import Work
    from app.models.exam_cycle import ExamCycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    assert _intermediate(client, "Рисунок", n=6).json()["created"] == 6

    files = [("photos", (f"extra{i}.jpg", _JPG_BYTES, "image/jpeg")) for i in range(5)]
    resp = client.post(
        "/upload/probnik/intermediate",
        data={"subject": "Рисунок"},
        files=files,
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["existing"] == 6
    assert body["remaining"] == 4
    assert body["limit"] == 10
    assert "Можно добавить ещё 4" in body["error"]

    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == user.id).first()
    assert db.query(Work).filter(
        Work.cycle_id == cycle.id,
        Work.is_final == False,  # noqa: E712
    ).count() == 6


def test_deleting_probnik_final_removes_stage_dependents_and_quota_recounts(
    client, session_factory, regular_user, admin_user, db
):
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle

    student_sess = session_factory(regular_user)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", student_sess.id)
    _create_active_period(db, regular_user, "mock_exam")
    _create_active_ticket(db, regular_user, "Рисунок")

    assert _intermediate(client, "Рисунок", n=10).json()["created"] == 10
    final_id = _final(client, "Рисунок").json()["work_ids"][0]
    cycle = db.query(ExamCycle).filter(ExamCycle.user_id == regular_user.id).first()
    assert db.query(Work).filter(
        Work.cycle_id == cycle.id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
    ).count() == 11

    client.cookies.set("session_id", admin_sess.id)
    resp_delete = client.delete(f"/cabinet/students/{regular_user.id}/works/{final_id}")
    assert resp_delete.status_code == 200
    assert resp_delete.json()["ok"] is True

    db.expire_all()
    assert db.query(Work).filter(Work.cycle_id == cycle.id).count() == 0

    client.cookies.set("session_id", student_sess.id)
    assert _intermediate(client, "Рисунок", n=6).json()["created"] == 6
    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    assert '"existing": 6' in resp.text
    assert '"remaining": 4' in resp.text
    assert '"limit": 10' in resp.text


def test_mock_exam_page_exposes_existing_stage_slots(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    assert _intermediate(client, "Рисунок", n=6).json()["created"] == 6

    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    assert "STAGE_STATE_BY_SUBJECT" in resp.text
    assert '"existing": 6' in resp.text
    assert '"remaining": 4' in resp.text
    assert '"limit": 10' in resp.text


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
    закрытым — повторная сдача 409. Открывается только следующим пробником."""
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
    assert resp.json()["error"] == "пробник уже оценён и закрыт"
    # Новый цикл не создан.
    assert db.query(ExamCycle).filter(ExamCycle.user_id == user.id).count() == 1


def test_probnik_reopens_with_new_assignment(auth_client, db):
    """Новый пробник открывает сдачу заново → создаётся новый цикл."""
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

    # Куратор/админ публикует НОВЫЙ пробник по тому же предмету.
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


def test_probnik_blocks_other_ticket_in_same_assignment_while_cycle_open(auth_client, db):
    """Финал по одному варианту закрывает остальные билеты текущего пробника."""
    from app.models.exam_cycle import ExamCycle

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    tickets = _create_active_ticket_set(db, user, "Рисунок", count=2)
    first_ticket, _second_ticket = tickets

    with patch("app.api.upload.random.choice", return_value=first_ticket):
        first_resp = _final(client, "Рисунок")
    assert first_resp.status_code == 200
    first_cycle_id = first_resp.json()["cycle_id"]

    # Цикл по first_ticket остаётся открытым: балл/ОС ещё не закрывали.
    first_cycle = db.query(ExamCycle).filter(ExamCycle.id == first_cycle_id).one()
    assert first_cycle.closed_at is None
    assert first_cycle.ticket_id == first_ticket.id

    second_resp = _final(client, "Рисунок")

    assert second_resp.status_code == 409
    assert second_resp.json()["error"] == "работа сдана, ждите обратной связи"

    cycles = (
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user.id)
        .order_by(ExamCycle.id)
        .all()
    )
    assert len(cycles) == 1
    assert cycles[0].ticket_id == first_ticket.id


# ── «На доработку» (revision) не снимает реальную блокировку пересдачи ───────

def test_revision_reopens_original_ticket_for_intermediate_photos(
    client, session_factory, regular_user, admin_user, db
):
    """После возврата этапные фото попадают в исходный цикл, не в другой билет."""
    from app.models.exam_cycle import ExamCycle
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    student_sess = session_factory(regular_user)
    admin_sess = session_factory(admin_user)
    client.cookies.set("session_id", student_sess.id)
    _create_active_period(db, regular_user, "mock_exam")
    first_ticket, second_ticket = _create_active_ticket_set(
        db, regular_user, "Рисунок", count=2
    )

    with patch("app.api.upload.random.choice", return_value=first_ticket):
        first_final = _final(client, "Рисунок")
    assert first_final.status_code == 200
    work_id = first_final.json()["work_ids"][0]
    original_cycle_id = first_final.json()["cycle_id"]

    client.cookies.set("session_id", admin_sess.id)
    returned = client.post(
        f"/cabinet/students/{regular_user.id}/mock-exams/{work_id}/revision"
    )
    assert returned.status_code == 200

    client.cookies.set("session_id", student_sess.id)
    # Даже если выбор следующего билета вернул бы другой вариант, должна
    # возобновиться исходная попытка по first_ticket.
    with patch("app.api.upload.random.choice", return_value=second_ticket):
        stage_upload = _intermediate(client, "Рисунок", n=1)

    assert stage_upload.status_code == 200
    assert stage_upload.json()["success"] is True
    assert db.query(ExamCycle).filter(ExamCycle.user_id == regular_user.id).count() == 1
    stage = db.query(Work).filter(
        Work.user_id == regular_user.id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.is_final == False,  # noqa: E712
    ).one()
    assert stage.cycle_id == original_cycle_id


def test_revision_reopens_submission_for_same_ticket(
    client, session_factory, regular_user, admin_user, db
):
    """Суперадмин шлёт пробник «на доработку» (POST .../mock-exams/{id}/revision).

    UI обещает студенту («Вернуть пробник на доработку? Студент сможет
    загрузить новые фото пробника.») и шлёт уведомление «Загрузи новые фото
    выполненного задания».

    revision выставляет Work.needs_revision=True на финале и снимает лок; повторная
    загрузка проходит через _overwrite_final: то же Work.id, тот же cycle_id и
    attempt_number, score/comment/needs_revision сброшены, диалог ОС не теряется.
    После redo финал снова «сдан» → СЛЕДУЮЩИЙ перезалив без новой revision блокируется
    (перезалив по своей воле запрещён). Открыть заново — только новой revision/билетом.
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

    # ...и реальный гейт (has_submitted_for_ticket) пропускает redo-загрузку, т.к.
    # needs_revision финал за сдачу не считает.
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

    # После redo финал снова «сдан» (needs_revision снят) → СЛЕДУЮЩИЙ перезалив без
    # новой revision запрещён: 409. Открыть заново можно только новой revision/билетом.
    resp3 = _final(client, "Рисунок",
                   photos=[("photos", ("redo2.jpg", _JPG_BYTES, "image/jpeg"))])
    assert resp3.status_code == 409
    assert resp3.json()["error"] == "работа сдана, ждите обратной связи"
    db.expire_all()
    final = db.query(Work).filter(Work.id == work_id).first()
    assert final.filename == "redo.jpg"  # не перезаписан повторно


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
    # ticket_title пробрасывается из ExamCycle.ticket_id -> ExamTicket.title
    assert '"ticket_title"' in resp.text
