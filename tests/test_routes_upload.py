"""Tests for /upload route — form GET and photo POST."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock


_MOCK_N8N = "app.api.upload.send_photo_to_n8n"
_MOCK_S3_UPLOAD = "app.api.upload.s3_service.upload_to_s3"
_MOCK_S3_CONFIGURED = "app.api.upload.s3_service.is_configured"
_OK_RESULT = {"success": True, "drive_file_id": "gdrive_abc"}

_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # minimal JPEG header


def _upload(client, files, month="январь", section=None, **kwargs):
    data = {"month": month}
    if section:
        data["section"] = section
    return client.post("/upload", data=data, files=files, **kwargs)


def _create_active_period(db, user, feature="mock_exam"):
    """Create an active FeaturePeriod covering today."""
    from app.models.feature_period import FeaturePeriod
    today = date.today()
    period = FeaturePeriod(
        feature=feature,
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=30),
        is_active=True,
        created_by_id=user.id,
    )
    db.add(period)
    db.commit()
    # Invalidate cache so the test sees fresh data
    from app.services.feature_periods import invalidate_feature_cache
    invalidate_feature_cache(feature)
    return period


def _create_active_ticket(db, user, subject="Рисунок", *, opens_at=None, closes_at=None):
    """Create an ExamAssignment + ExamTicket (assign_to_all=True) valid today.

    opens_at/closes_at — точный период доступа (datetime, МСК). Если не заданы,
    билет хранит только даты, и окно непрерывно тянется start-1д 11:45..end+30д 18:30.
    """
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
    """Create one active assignment with several tickets for random distribution."""
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


# ---------------------------------------------------------------------------
# GET /upload
# ---------------------------------------------------------------------------

def test_upload_form_requires_auth(client):
    resp = client.get("/upload", follow_redirects=False)
    assert resp.status_code == 302


def test_upload_form_with_auth_returns_200(auth_client):
    client, _ = auth_client
    resp = client.get("/upload")
    assert resp.status_code == 200


def test_upload_form_contains_month_options(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")
    resp = client.get("/upload")
    assert "январь" in resp.text
    assert "декабрь" in resp.text


def test_upload_form_allows_explicit_before_mode_even_after_completion(auth_client, db):
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()

    resp = client.get("/upload?section=before")
    assert resp.status_code == 200
    assert "Раздел «До»" in resp.text
    assert "Выбери месяц" not in resp.text


# ---------------------------------------------------------------------------
# POST /upload — validation
# ---------------------------------------------------------------------------

def test_upload_invalid_month_shows_error(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _upload(
            client,
            [("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))],
            month="martbar",
            section="after",
        )
    assert resp.status_code == 200
    assert "месяц" in resp.text.lower()


def test_upload_unsupported_format_shows_error(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")
    resp = _upload(client, [("photos", ("doc.pdf", b"data", "application/pdf"))])
    assert resp.status_code == 200
    assert "формат" in resp.text.lower() or "неподдерживаемый" in resp.text


def test_upload_too_large_file_shows_error(auth_client):
    client, _ = auth_client
    huge = b"\xff\xd8\xff" + b"X" * (11 * 1024 * 1024)  # 11 MB
    resp = _upload(client, [("photos", ("big.jpg", huge, "image/jpeg"))])
    assert resp.status_code == 200
    assert "большой" in resp.text or "10" in resp.text


def test_upload_too_many_files_shows_error(auth_client):
    client, _ = auth_client
    files = [("photos", (f"p{i}.jpg", _JPG_BYTES, "image/jpeg")) for i in range(12)]
    resp = _upload(client, files)
    assert resp.status_code == 200
    assert "максимум" in resp.text.lower() or "10" in resp.text


# ---------------------------------------------------------------------------
# POST /upload — success path
# ---------------------------------------------------------------------------

def test_upload_single_photo_success(auth_client):
    client, _ = auth_client
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _upload(client, [("photos", ("photo.jpg", _JPG_BYTES, "image/jpeg"))])
    assert resp.status_code == 200
    assert "1" in resp.text  # success_count shown


def test_upload_multiple_photos_success(auth_client):
    client, _ = auth_client
    files = [("photos", (f"p{i}.jpg", _JPG_BYTES, "image/jpeg")) for i in range(3)]
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _upload(client, files)
    assert resp.status_code == 200
    # 3 successful uploads
    assert "3" in resp.text


def test_upload_png_accepted(auth_client):
    client, _ = auth_client
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _upload(client, [("photos", ("img.png", png, "image/png"))])
    assert resp.status_code == 200
    assert "формат" not in resp.text.lower()


def test_upload_s3_failure_shows_retry_error(auth_client, db):
    """When S3 is configured but upload fails, user sees error and no Work record is created."""
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")
    with patch(_MOCK_S3_CONFIGURED, return_value=True), \
         patch(_MOCK_S3_UPLOAD, return_value=None), \
         patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _upload(client, [("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))])

    assert resp.status_code == 200
    assert "попробуйте" in resp.text.lower() or "хранилище" in resp.text.lower()
    # No Work record should be created
    assert db.query(Work).filter(Work.user_id == user.id).count() == 0


def test_upload_n8n_failure_still_shows_success(auth_client, db):
    """An n8n exception cannot roll back S3 or change the upload API result."""
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")

    with patch(_MOCK_S3_CONFIGURED, return_value=True), \
         patch(_MOCK_S3_UPLOAD, return_value="https://s3.example/work.jpg"), \
         patch(_MOCK_N8N, new_callable=AsyncMock, side_effect=RuntimeError("n8n unavailable")), \
         patch("app.api.upload._N8N_RETRY_DELAYS", [0, 0]):
        resp = client.post(
            "/upload/api",
            data={"month": "январь", "section": "after"},
            files=[("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))],
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "created": 1,
        "failed": 0,
        "error": None,
        "mode_changed": False,
    }

    db.expire_all()
    work = db.query(Work).filter(Work.user_id == user.id).one()
    assert work.status == "success"
    assert work.s3_url == "https://s3.example/work.jpg"
    assert work.drive_status == "failed"


def test_upload_background_scheduling_failure_keeps_s3_success(auth_client, db):
    """Even failure to register the n8n task is secondary to committed S3 data."""
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")

    with patch(_MOCK_S3_CONFIGURED, return_value=True), \
         patch(_MOCK_S3_UPLOAD, return_value="https://s3.example/work.jpg"), \
         patch(
             "app.api.upload.BackgroundTasks.add_task",
             side_effect=RuntimeError("background queue unavailable"),
         ):
        resp = client.post(
            "/upload/api",
            data={"month": "январь", "section": "after"},
            files=[("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))],
        )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["created"] == 1
    assert resp.json()["failed"] == 0

    db.expire_all()
    work = db.query(Work).filter(Work.user_id == user.id).one()
    assert work.status == "success"
    assert work.s3_url == "https://s3.example/work.jpg"
    assert work.drive_status == "failed"


def test_upload_writes_to_upload_log(auth_client, db):
    from app.models.upload_log import UploadLog

    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        _upload(client, [("photos", ("x.jpg", _JPG_BYTES, "image/jpeg"))])

    logs = db.query(UploadLog).filter(UploadLog.user_id == user.id).all()
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert logs[0].month == "январь"


def test_upload_before_without_month_succeeds(auth_client, db):
    from app.models.user import User
    from app.models.work import Work, WORK_TYPE_BEFORE

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()

    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = client.post(
            "/upload",
            data={"section": "before"},
            files=[("photos", ("before.jpg", _JPG_BYTES, "image/jpeg"))],
        )

    assert resp.status_code == 200
    works = db.query(Work).filter(Work.user_id == user.id, Work.work_type == WORK_TYPE_BEFORE).all()
    assert len(works) == 1


def test_upload_explicit_before_keeps_work_type_before(auth_client, db):
    from app.models.user import User
    from app.models.work import Work, WORK_TYPE_BEFORE

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()

    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _upload(
            client,
            [("photos", ("before.jpg", _JPG_BYTES, "image/jpeg"))],
            section="before",
        )

    assert resp.status_code == 200
    works = db.query(Work).filter(Work.user_id == user.id).all()
    assert any(work.work_type == WORK_TYPE_BEFORE for work in works)


# ---------------------------------------------------------------------------
# GET /upload/mock-exam
# ---------------------------------------------------------------------------

def test_mock_exam_form_requires_auth(client):
    resp = client.get("/upload/mock-exam", follow_redirects=False)
    assert resp.status_code == 302


def test_mock_exam_form_with_auth_returns_200(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    resp = client.get("/upload/mock-exam")
    assert resp.status_code == 200
    assert "Рисунок" in resp.text
    assert "Композиция" in resp.text


def test_mock_exam_csrf_requires_auth(client):
    # Фронт шлёт Accept: application/json → хендлер 401 отдаёт JSON (не редирект),
    # чтобы refreshCsrf() увидел статус 401 и показал «сессия истекла».
    resp = client.get("/upload/mock-exam/csrf", headers={"Accept": "application/json"})
    assert resp.status_code == 401


def test_mock_exam_csrf_returns_valid_token(auth_client):
    """Эндпоинт отдаёт свежий CSRF-токен, валидный для текущей сессии.

    Фронт дёргает его перед каждой отправкой/heartbeat, чтобы долгая страница
    пробника не словила «Неверный CSRF-токен»/«Сессия истекла».
    """
    from app.csrf import validate_csrf_token
    client, _ = auth_client
    resp = client.get("/upload/mock-exam/csrf")
    assert resp.status_code == 200
    token = resp.json()["csrf_token"]
    assert token
    session_values = [cookie.value for cookie in client.cookies.jar if cookie.name == "session_id"]
    assert len(set(session_values)) == 1
    session_id = session_values[0]
    assert validate_csrf_token(session_id, token)


def test_mock_exam_form_force_refreshes_session_cookie(client, db, regular_user, session_factory):
    sess = session_factory(regular_user, hours=24)
    before = sess.expires_at
    client.cookies.set("session_id", sess.id)

    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    assert "session_id=" in resp.headers.get("set-cookie", "")
    db.refresh(sess)
    refreshed_at = sess.expires_at
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
    if before.tzinfo is None:
        before = before.replace(tzinfo=timezone.utc)
    assert refreshed_at > before
    assert refreshed_at > datetime.now(timezone.utc) + timedelta(hours=23)


def test_mock_exam_locked_subjects_do_not_disable_form(auth_client, db):
    """GET /upload/mock-exam keeps locked subjects available for a new period."""
    from app.models.mock_exam_lock import MockExamLock
    from datetime import datetime, timezone
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    db.add(MockExamLock(user_id=user.id, subject="Рисунок", is_locked=True,
                        locked_at=datetime.now(timezone.utc)))
    db.commit()
    resp = client.get("/upload/mock-exam")
    assert resp.status_code == 200
    assert "уже сдано" not in resp.text


def test_mock_exam_current_period_submission_locks_subject(auth_client, db):
    """Сданный по текущему билету пробник в ОТКРЫТОМ цикле БЛОКИРУЕТ предмет:
    подсказка «работа сдана · ждите ОС», кнопка недоступна (перезалив по своей воле
    запрещён — открыть заново можно только следующим билетом/новой revision)."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle
    from datetime import datetime, timezone

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    ticket = _create_active_ticket(db, user, "Рисунок")
    cycle = ExamCycle(user_id=user.id, subject="Рисунок", ticket_id=ticket.id,
                      started_at=datetime.now(timezone.utc))
    db.add(cycle)
    db.flush()
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        s3_url="https://example.test/mock.jpg",
        subject="Рисунок",
        tariff=user.tariff,
        status="success",
        score=None,
        cycle_id=cycle.id,
        is_final=True,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    # открытый сданный цикл → предмет заблокирован с подсказкой «ждите ОС»
    assert "работа сдана · ждите ОС" in resp.text
    assert "subject-locked" in resp.text
    # режима свободного перезалива больше нет
    assert "можно перезалить" not in resp.text


def test_mock_exam_start_skips_submitted_ticket_when_another_ticket_is_active(auth_client, db):
    """Если один билет уже сдан в открытом цикле, другой активный билет всё равно выдаётся."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle
    from app.models.mock_exam_attempt import MockExamAttempt
    from datetime import datetime, timezone

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    submitted_ticket, next_ticket = _create_active_ticket_set(db, user, "Рисунок", count=2)
    cycle = ExamCycle(
        user_id=user.id,
        subject="Рисунок",
        ticket_id=submitted_ticket.id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(cycle)
    db.flush()
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        s3_url="https://example.test/mock.jpg",
        subject="Рисунок",
        tariff=user.tariff,
        status="success",
        score=None,
        cycle_id=cycle.id,
        is_final=True,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket"]["id"] == next_ticket.id
    assert body["resumed"] is False
    attempt = db.query(MockExamAttempt).filter(MockExamAttempt.user_id == user.id).one()
    assert attempt.ticket_id == next_ticket.id


def test_mock_exam_revision_unblocks_subject_in_form(auth_client, db):
    """Финал с needs_revision=True (отправлен «на доработку») не считается
    блокировкой в _locked_mock_subjects — кнопка предмета доступна для пересдачи,
    синхронно с has_submitted_for_ticket (реальный гейт в upload_probnik_final)."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle
    from datetime import datetime, timezone

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    ticket = _create_active_ticket(db, user, "Рисунок")
    cycle = ExamCycle(user_id=user.id, subject="Рисунок", ticket_id=ticket.id,
                      started_at=datetime.now(timezone.utc))
    db.add(cycle)
    db.flush()
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        subject="Рисунок",
        tariff=user.tariff,
        status="success",
        score=None,
        cycle_id=cycle.id,
        is_final=True,
        needs_revision=True,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    assert "работа сдана, ждите ОС" not in resp.text


def test_mock_exam_intermediate_only_cycle_does_not_lock_subject(auth_client, db):
    """Цикл с этапными фото, но без финала (например, финал не дошёл из-за обрыва
    сессии) не должен блокировать предмет — ученик может попробовать сдать снова."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from app.models.exam_cycle import ExamCycle
    from datetime import datetime, timezone

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    ticket = _create_active_ticket(db, user, "Рисунок")
    cycle = ExamCycle(user_id=user.id, subject="Рисунок", ticket_id=ticket.id,
                      started_at=datetime.now(timezone.utc))
    db.add(cycle)
    db.flush()
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="stage1.jpg",
        subject="Рисунок",
        tariff=user.tariff,
        status="success",
        score=None,
        cycle_id=cycle.id,
        is_final=False,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    assert "работа сдана, ждите ОС" not in resp.text


def test_mock_exam_old_period_submission_does_not_show_waiting_grade(auth_client, db):
    """Old unchecked submissions should not block a new active period."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timedelta, timezone

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="old_mock.jpg",
        subject="Рисунок",
        tariff=user.tariff,
        status="success",
        score=None,
        created_at=datetime.now(timezone.utc) - timedelta(days=10),
    ))
    db.commit()

    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    assert "уже сдано" not in resp.text


def test_mock_exam_locked_subject_can_be_started(auth_client, db):
    """POST /upload/mock-exam/start ignores old review locks when the period is open."""
    from app.models.mock_exam_lock import MockExamLock
    from datetime import datetime, timezone
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    db.add(MockExamLock(user_id=user.id, subject="Рисунок", is_locked=True,
                        locked_at=datetime.now(timezone.utc)))
    db.commit()

    resp = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})

    assert resp.status_code == 200
    assert resp.json()["subject"] == "Рисунок"
    assert resp.json()["resumed"] is False


def test_mock_exam_start_resumes_when_ticket_still_active(auth_client, db):
    """Повторный POST /upload/mock-exam/start резюмирует попытку, если её
    билет всё ещё активен (период не истёк, задание опубликовано)."""
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")

    resp1 = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1["resumed"] is False
    attempt_id = body1["attempt_id"]

    resp2 = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["resumed"] is True
    assert body2["attempt_id"] == attempt_id


def test_mock_exam_start_randomizes_between_current_assignment_tickets(auth_client, db):
    from app.models.mock_exam_attempt import MockExamAttempt

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    tickets = _create_active_ticket_set(db, user, "Рисунок", count=2)
    chosen = tickets[0]

    with patch("app.api.upload.random.choice", return_value=chosen) as choice:
        resp = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})

    assert resp.status_code == 200
    body = resp.json()
    assert choice.called
    assert body["ticket"]["id"] == chosen.id

    attempt = db.query(MockExamAttempt).filter(MockExamAttempt.user_id == user.id).one()
    assert attempt.ticket_id == chosen.id


def test_mock_exam_start_does_not_resume_expired_ticket_attempt(auth_client, db):
    """Открытая попытка со снимком билета из АРХИВНОГО задания не резюмируется:
    помечается expired_at (не completed_at), и стартует новая попытка с
    актуальным билетом. Регрессия инцидента user_id=134 (билет №3 архивного
    задания возвращался вместо актуального билета №1)."""
    from app.models.exam_assignment import ExamAssignment, ExamTicket
    from app.models.mock_exam_attempt import MockExamAttempt

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")

    today = date.today()
    old_assignment = ExamAssignment(
        title="Старое задание", subject="Композиция",
        created_by_id=user.id, status="archived",
    )
    db.add(old_assignment)
    db.flush()
    old_ticket = ExamTicket(
        assignment_id=old_assignment.id, ticket_number=3,
        title="Билет №3 (архивный)",
        start_date=today - timedelta(days=40),
        end_date=today - timedelta(days=30),
        assign_to_all=True,
    )
    db.add(old_ticket)
    db.flush()

    stale_attempt = MockExamAttempt(
        user_id=user.id,
        subject="Композиция",
        ticket_id=old_ticket.id,
        ticket_title=old_ticket.title,
        ticket_description="старое описание",
    )
    db.add(stale_attempt)
    db.commit()
    stale_id = stale_attempt.id

    new_ticket = _create_active_ticket(db, user, "Композиция")

    resp = client.post("/upload/mock-exam/start", data={"subject": "Композиция"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resumed"] is False
    assert body["ticket"]["id"] == new_ticket.id
    assert body["attempt_id"] != stale_id

    db.expire_all()
    stale = db.query(MockExamAttempt).filter(MockExamAttempt.id == stale_id).first()
    assert stale.expired_at is not None
    assert stale.completed_at is None


def test_mock_exam_start_open_any_time_of_day_within_window(auth_client, db, monkeypatch):
    """Внутри непрерывного периода доступа старт открыт в любое время суток —
    «время на выполнение» больше не запирает выдачу билета по часам."""
    from app.services import mock_exam_access
    from app.services.tz import now_msk

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")  # date-only → непрерывное многодневное окно
    # 22:00 сегодня внутри окна start-1д 11:45..end+30д 18:30.
    late = now_msk().replace(hour=22, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(mock_exam_access, "now_msk", lambda: late)

    resp = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})

    assert resp.status_code == 200
    assert resp.json()["subject"] == "Рисунок"
    assert resp.json()["ticket"]["id"] is not None


def test_mock_exam_start_blocked_after_window_closes(auth_client, db):
    """Период доступа: после closes_at выдача билета закрыта."""
    from app.services.tz import now_msk

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _now = now_msk()
    _create_active_ticket(
        db, user, "Рисунок",
        opens_at=_now - timedelta(days=20),
        closes_at=_now - timedelta(days=10),  # окно закрылось ~10 дней назад
    )

    resp = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})

    assert resp.status_code in (403, 404)


def test_mock_exam_start_blocked_within_duration_before_close(auth_client, db, monkeypatch):
    """Нельзя получить билет в последние «время на выполнение» минут периода.

    Период (date-only) 11:45–18:30, выполнение 90 мин → последний старт 17:00.
    В 17:30 старт закрыт (403 start_window_closed), хотя сдача ещё открыта.
    Билет date-only (Date-колонки) — без tz-сдвига SQLite, тест детерминирован.
    """
    from datetime import datetime, date as _date
    from app.models.exam_assignment import ExamAssignment, ExamTicket
    from app.services import mock_exam_access
    from app.services.tz import MSK_TZ

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    assignment = ExamAssignment(
        title="t", subject="Рисунок", created_by_id=user.id, status="published",
    )
    db.add(assignment)
    db.flush()
    day = _date(2026, 6, 1)
    db.add(ExamTicket(
        assignment_id=assignment.id, ticket_number=1, title="Билет Рисунок",
        start_date=day, end_date=day,  # окно по fallback 11:45..18:30
        duration_minutes=90, assign_to_all=True,
    ))
    db.commit()
    # 17:30 — после отсечки старта (18:30 − 90 мин = 17:00), но до закрытия 18:30.
    monkeypatch.setattr(
        mock_exam_access, "now_msk",
        lambda: datetime(2026, 6, 1, 17, 30, tzinfo=MSK_TZ),
    )

    resp = client.post("/upload/mock-exam/start", data={"subject": "Рисунок"})

    assert resp.status_code == 403
    assert resp.json()["error"] == "start_window_closed"


def test_mock_exam_start_rejects_subject_not_allowed_by_profile(auth_client, db):
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"exam_subjects": "Р"})
    db.commit()
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Композиция")

    resp = client.post("/upload/mock-exam/start", data={"subject": "Композиция"})

    assert resp.status_code == 403
    assert resp.json()["error"] == "subject_forbidden"


def test_retake_form_available_when_mock_sent_to_retake(auth_client, db):
    """A personal retake assignment allows upload even when the global retake period is closed."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timezone

    client, user = auth_client
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        subject="Рисунок",
        tariff=user.tariff,
        status="success",
        score=60,
        sent_to_retake=True,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.get("/upload/retake")

    assert resp.status_code == 200
    assert "Отработки закрыты" not in resp.text
    assert "Сдать отработку" in resp.text


def test_retake_api_accepts_upload_when_mock_sent_to_retake(auth_client, db):
    """POST /upload/retake/api is allowed by a personal retake assignment."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
    from datetime import datetime, timezone

    client, user = auth_client
    db.add(Work(
        user_id=user.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        subject="Рисунок",
        tariff=user.tariff,
        status="success",
        score=60,
        sent_to_retake=True,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = client.post(
            "/upload/retake/api",
            data={"student_score": "60", "subject": "Рисунок"},
            files=[("photos", ("retake.jpg", _JPG_BYTES, "image/jpeg"))],
        )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert db.query(Work).filter(
        Work.user_id == user.id,
        Work.work_type == WORK_TYPE_RETAKE,
    ).count() == 1


def test_send_mock_to_retake_keeps_subject_locked(admin_client, db, user_factory):
    """'На отработку' saves a score and retake assignment, but does not unlock mock reupload."""
    from app.models.mock_exam_lock import MockExamLock
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timezone

    client, _ = admin_client
    student = user_factory(vk_id=151_515, name="Student Retake", role_name="ученик")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        subject="Рисунок",
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.add(MockExamLock(
        user_id=student.id,
        subject="Рисунок",
        is_locked=True,
        locked_at=datetime.now(timezone.utc),
    ))
    db.commit()
    db.refresh(work)

    resp = client.post(
        f"/cabinet/students/{student.id}/mock-exams/{work.id}/retake",
        data={"score": "60", "comment": "Переделать штриховку"},
    )

    assert resp.status_code == 200
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == student.id,
        MockExamLock.subject == "Рисунок",
    ).first()
    db.refresh(work)
    assert work.sent_to_retake is True
    assert lock.is_locked is True
    assert lock.unlocked_by_id is None


def test_send_mock_to_revision_unlocks_subject_without_score(admin_client, db, user_factory):
    """'На доработку' unlocks mock reupload without setting score or retake assignment."""
    from app.models.mock_exam_lock import MockExamLock
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timezone

    client, admin = admin_client
    student = user_factory(vk_id=161_616, name="Student Revision", role_name="ученик")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        subject="Рисунок",
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.add(MockExamLock(
        user_id=student.id,
        subject="Рисунок",
        is_locked=True,
        locked_at=datetime.now(timezone.utc),
    ))
    db.commit()
    db.refresh(work)

    resp = client.post(
        f"/cabinet/students/{student.id}/mock-exams/{work.id}/revision",
        data={},
    )

    assert resp.status_code == 200
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == student.id,
        MockExamLock.subject == "Рисунок",
    ).first()
    db.refresh(work)
    assert work.score is None
    assert work.sent_to_retake is False
    assert lock.is_locked is False
    assert lock.unlocked_by_id == admin.id


def test_regular_admin_cannot_send_mock_to_revision(client, db, user_factory, session_factory):
    """Only superadmin can use 'На доработку'."""
    from app.models.mock_exam_lock import MockExamLock
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timezone

    admin = user_factory(vk_id=171_717, name="Regular Admin", role_name="админ")
    student = user_factory(vk_id=181_818, name="Student Revision Denied", role_name="ученик")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        subject="Рисунок",
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.add(MockExamLock(
        user_id=student.id,
        subject="Рисунок",
        is_locked=True,
        locked_at=datetime.now(timezone.utc),
    ))
    db.commit()
    db.refresh(work)

    sess = session_factory(admin)
    client.cookies.set("session_id", sess.id)
    resp = client.post(
        f"/cabinet/students/{student.id}/mock-exams/{work.id}/revision",
        data={},
    )

    assert resp.status_code == 403


def test_superadmin_can_move_unassigned_retake_to_subject(admin_client, db, user_factory):
    """Superadmin can assign a subject to retake works that arrived without one."""
    from app.models.work import Work, WORK_TYPE_RETAKE
    from datetime import datetime, timezone

    client, _ = admin_client
    student = user_factory(vk_id=181_819, name="Student Retake Move", role_name="ученик")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_RETAKE,
        month="январь",
        year=2026,
        filename="retake.jpg",
        subject=None,
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.commit()
    db.refresh(work)

    resp = client.post(
        f"/cabinet/students/{student.id}/retakes/{work.id}/subject",
        data={"subject": "Рисунок"},
    )

    assert resp.status_code == 200
    db.refresh(work)
    assert work.subject == "Рисунок"


def test_regular_admin_cannot_move_retake_to_subject(client, db, user_factory, session_factory):
    """Only superadmin can move retake works between subject sections."""
    from app.models.work import Work, WORK_TYPE_RETAKE
    from datetime import datetime, timezone

    admin = user_factory(vk_id=181_820, name="Regular Admin Retake", role_name="админ")
    student = user_factory(vk_id=181_821, name="Student Retake Denied", role_name="ученик")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_RETAKE,
        month="январь",
        year=2026,
        filename="retake.jpg",
        subject=None,
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.commit()
    db.refresh(work)

    sess = session_factory(admin)
    client.cookies.set("session_id", sess.id)
    resp = client.post(
        f"/cabinet/students/{student.id}/retakes/{work.id}/subject",
        data={"subject": "Рисунок"},
    )

    assert resp.status_code == 403
    db.refresh(work)
    assert work.subject is None


def test_move_retake_to_subject_changes_only_selected_photo(admin_client, db, user_factory):
    """Moving one unassigned retake photo must not move the whole month/group."""
    from app.models.work import Work, WORK_TYPE_RETAKE
    from datetime import datetime, timezone

    client, _ = admin_client
    student = user_factory(vk_id=181_822, name="Student Retake Split", role_name="ученик")
    first = Work(
        user_id=student.id,
        work_type=WORK_TYPE_RETAKE,
        month="январь",
        year=2026,
        filename="first.jpg",
        subject=None,
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    second = Work(
        user_id=student.id,
        work_type=WORK_TYPE_RETAKE,
        month="январь",
        year=2026,
        filename="second.jpg",
        subject=None,
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add_all([first, second])
    db.commit()
    db.refresh(first)
    db.refresh(second)

    resp = client.post(
        f"/cabinet/students/{student.id}/retakes/{first.id}/subject",
        data={"subject": "Рисунок"},
    )

    assert resp.status_code == 200
    db.refresh(first)
    db.refresh(second)
    assert first.subject == "Рисунок"
    assert second.subject is None


def test_students_retakes_endpoint_reports_scored_retake(admin_client, db, user_factory):
    """The staff retakes endpoint exposes the curator score for a scored retake."""
    from app.models.work import Work, WORK_TYPE_RETAKE
    from datetime import datetime, timezone

    client, _ = admin_client
    student = user_factory(vk_id=181_823, name="Student Retake Checked")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_RETAKE,
        month="январь",
        year=2026,
        filename="checked.jpg",
        subject="Рисунок",
        tariff=student.tariff,
        status="success",
        score=90,
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.commit()

    resp = client.get(f"/cabinet/students/{student.id}/retakes")

    assert resp.status_code == 200
    rows = resp.json()["retakes_by_subject"]["Рисунок"]
    assert len(rows) == 1
    assert rows[0]["curator_score"] == 90.0


def test_students_retakes_endpoint_reports_unscored_retake(admin_client, db, user_factory):
    """An unscored retake is returned with no curator score."""
    from app.models.work import Work, WORK_TYPE_RETAKE
    from datetime import datetime, timezone

    client, _ = admin_client
    student = user_factory(vk_id=181_824, name="Student Retake Pending")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_RETAKE,
        month="январь",
        year=2026,
        filename="pending.jpg",
        subject="Рисунок",
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.commit()

    resp = client.get(f"/cabinet/students/{student.id}/retakes")

    assert resp.status_code == 200
    rows = resp.json()["retakes_by_subject"]["Рисунок"]
    assert len(rows) == 1
    assert rows[0]["curator_score"] is None


def test_revision_opens_mock_exam_upload_again(client, db, user_factory, session_factory):
    """After superadmin sends to revision, the student can open the mock exam upload again."""
    from app.models.mock_exam_lock import MockExamLock
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    from datetime import datetime, timezone

    superadmin = user_factory(vk_id=191_919, name="Super Admin", role_name="суперадмин")
    student = user_factory(vk_id=202_020, name="Student Reupload", role_name="ученик")
    _create_active_period(db, superadmin, "mock_exam")
    _create_active_ticket(db, superadmin, "Рисунок")
    work = Work(
        user_id=student.id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="январь",
        year=2026,
        filename="mock.jpg",
        subject="Рисунок",
        tariff=student.tariff,
        status="success",
        created_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.add(MockExamLock(
        user_id=student.id,
        subject="Рисунок",
        is_locked=True,
        locked_at=datetime.now(timezone.utc),
    ))
    db.commit()
    db.refresh(work)

    super_session = session_factory(superadmin)
    client.cookies.set("session_id", super_session.id)
    revision_resp = client.post(
        f"/cabinet/students/{student.id}/mock-exams/{work.id}/revision",
        data={},
    )
    assert revision_resp.status_code == 200

    student_session = session_factory(student)
    client.cookies.set("session_id", student_session.id)
    form_resp = client.get("/upload/mock-exam")

    assert form_resp.status_code == 200
    assert "уже сдано" not in form_resp.text
    assert "Рисунок" in form_resp.text


def test_curator_can_unlock_subject(client, db, user_factory, session_factory):
    """Curator POSTing /cabinet/mock-exam/unlock sets is_locked=False."""
    from app.models.mock_exam_lock import MockExamLock
    from datetime import datetime, timezone
    student = user_factory(vk_id=100_001, role_name="ученик")
    curator = user_factory(vk_id=200_001, name="Curator Test", role_name="куратор")

    # Assign student to curator so IDOR check passes
    student.curator_id = curator.id
    db.commit()

    _create_active_ticket(db, curator, "Рисунок")  # created_by_id requires a valid user
    lock = MockExamLock(user_id=student.id, subject="Рисунок", is_locked=True,
                        locked_at=datetime.now(timezone.utc))
    db.add(lock)
    db.commit()
    db.refresh(lock)
    lock_id = lock.id

    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)

    resp = client.post(
        "/cabinet/mock-exam/unlock",
        data={"student_id": student.id, "subject": "Рисунок"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    updated = db.query(MockExamLock).filter(MockExamLock.id == lock_id).first()
    assert updated.is_locked is False
    assert updated.unlocked_by_id == curator.id
