"""Tests for /upload route — form GET and photo POST."""
from datetime import date, timedelta
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


def _create_active_ticket(db, user, subject="Рисунок"):
    """Create an ExamAssignment + ExamTicket (assign_to_all=True) valid today."""
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
    """n8n runs in background — user sees success even if Drive fails."""
    from app.models.work import Work
    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")
    fail_result = {"success": False, "error": "Google Drive quota exceeded"}
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=fail_result):
        resp = _upload(client, [("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))])
    assert resp.status_code == 200
    # Work record must still be created (S3 succeeded)
    works = db.query(Work).filter(Work.user_id == user.id).all()
    assert len(works) >= 1


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


def test_mock_exam_current_period_submission_shows_waiting_grade(auth_client, db):
    """Сданный по текущему билету пробник (открытый цикл) блокирует кнопку предмета
    с подсказкой «работа сдана, ждите ОС» (модель «одна сдача на билет»)."""
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
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.get("/upload/mock-exam")

    assert resp.status_code == 200
    assert "работа сдана, ждите ОС" in resp.text


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
