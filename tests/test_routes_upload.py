"""Tests for /upload route — form GET and photo POST."""
from io import BytesIO
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch, MagicMock


_MOCK_N8N = "app.api.upload.send_photo_to_n8n"
_MOCK_S3_UPLOAD = "app.api.upload.s3_service.upload_to_s3"
_MOCK_S3_CONFIGURED = "app.api.upload.s3_service.is_configured"
_OK_RESULT = {"success": True, "drive_file_id": "gdrive_abc"}

_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # minimal JPEG header


def _upload(client, files, month="январь", **kwargs):
    return client.post("/upload", data={"month": month}, files=files, **kwargs)


def _truncated_jpg_bytes(cut: int = 1) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (120, 120), "red").save(buf, format="JPEG")
    data = buf.getvalue()
    return data[:-cut]


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


# ---------------------------------------------------------------------------
# POST /upload — validation
# ---------------------------------------------------------------------------

def test_upload_invalid_month_shows_error(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "portfolio_upload")
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _upload(client, [("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))], month="martbar")
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


# ---------------------------------------------------------------------------
# POST /upload/mock-exam — validation
# ---------------------------------------------------------------------------

def _mock_upload(client, files, subject="Рисунок", **kwargs):
    return client.post("/upload/mock-exam", data={"subject": subject}, files=files, **kwargs)


def test_mock_exam_invalid_subject_shows_error(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    resp = _mock_upload(client, [("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))], subject="Физика")
    assert resp.status_code == 200
    assert "предмет" in resp.text.lower()


def test_mock_exam_no_photos_rejected(auth_client):
    # FastAPI returns 422 when required `photos` field has no valid file.
    # The form's `required` attribute prevents this on the client side.
    client, _ = auth_client
    resp = client.post("/upload/mock-exam", data={"subject": "Рисунок"},
                       files=[("photos", ("", b"", "image/jpeg"))])
    assert resp.status_code in (200, 422)


def test_mock_exam_unsupported_format_shows_error(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    resp = _mock_upload(client, [("photos", ("doc.pdf", b"data", "application/pdf"))])
    assert resp.status_code == 200
    assert "формат" in resp.text.lower() or "неподдерживаемый" in resp.text


def test_mock_exam_too_large_shows_error(auth_client):
    client, _ = auth_client
    huge = b"\xff\xd8\xff" + b"X" * (11 * 1024 * 1024)
    resp = _mock_upload(client, [("photos", ("big.jpg", huge, "image/jpeg"))])
    assert resp.status_code == 200
    assert "большой" in resp.text or "10" in resp.text


# ---------------------------------------------------------------------------
# POST /upload/mock-exam — success path
# ---------------------------------------------------------------------------

def test_mock_exam_success(auth_client, db):
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _mock_upload(client, [("photos", ("work.jpg", _JPG_BYTES, "image/jpeg"))])
    assert resp.status_code == 200
    assert "1" in resp.text  # success_count shown


def test_mock_exam_truncated_jpeg_still_uploads(auth_client, db):
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    truncated = _truncated_jpg_bytes(1)

    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _mock_upload(client, [("photos", ("work.jpg", truncated, "image/jpeg"))])

    assert resp.status_code == 200
    assert "не удалось загрузить" not in resp.text.lower()

    works = db.query(Work).filter(
        Work.user_id == user.id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
    ).all()
    assert len(works) == 1


def test_mock_exam_writes_work_record(auth_client, db):
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Композиция")
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        _mock_upload(client, [("photos", ("w.jpg", _JPG_BYTES, "image/jpeg"))], subject="Композиция")

    works = db.query(Work).filter(Work.user_id == user.id).all()
    assert len(works) == 1
    assert works[0].work_type == WORK_TYPE_MOCK_EXAM
    assert works[0].subject == "Композиция"
    assert works[0].status == "success"


def test_mock_exam_s3_failure_shows_retry_error(auth_client, db):
    """When S3 is configured but upload fails, mock exam shows retry error, no Work record."""
    from app.models.work import Work

    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    with patch(_MOCK_S3_CONFIGURED, return_value=True), \
         patch(_MOCK_S3_UPLOAD, return_value=None), \
         patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _mock_upload(client, [("photos", ("w.jpg", _JPG_BYTES, "image/jpeg"))])

    assert resp.status_code == 200
    assert "попробуйте" in resp.text.lower() or "хранилище" in resp.text.lower()
    assert db.query(Work).filter(Work.user_id == user.id).count() == 0


def test_mock_exam_n8n_failure_still_shows_success(auth_client, db):
    """n8n runs in background — user sees success even if Drive fails."""
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    fail = {"success": False, "error": "Drive quota exceeded"}
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=fail):
        resp = _mock_upload(client, [("photos", ("p.jpg", _JPG_BYTES, "image/jpeg"))])
    assert resp.status_code == 200
    works = db.query(Work).filter(Work.user_id == user.id, Work.work_type == WORK_TYPE_MOCK_EXAM).all()
    assert len(works) >= 1


# ---------------------------------------------------------------------------
# Mock exam locks
# ---------------------------------------------------------------------------

def test_mock_exam_locks_subject_after_submission(auth_client, db):
    """After successful upload MockExamLock is created with is_locked=True."""
    from app.models.mock_exam_lock import MockExamLock
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        _mock_upload(client, [("photos", ("w.jpg", _JPG_BYTES, "image/jpeg"))], subject="Рисунок")
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == user.id,
        MockExamLock.subject == "Рисунок",
    ).first()
    assert lock is not None
    assert lock.is_locked is True


def test_mock_exam_locked_subjects_shown_on_form(auth_client, db):
    """GET /upload/mock-exam with a locked subject renders it as disabled."""
    from app.models.mock_exam_lock import MockExamLock
    from datetime import datetime, timezone
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    db.add(MockExamLock(user_id=user.id, subject="Рисунок", is_locked=True,
                        locked_at=datetime.now(timezone.utc)))
    db.commit()
    resp = client.get("/upload/mock-exam")
    assert resp.status_code == 200
    assert "subject-locked" in resp.text or 'disabled' in resp.text


def test_mock_exam_locked_subject_cannot_be_submitted(auth_client, db):
    """POST with a locked subject returns an error, no new Work record."""
    from app.models.mock_exam_lock import MockExamLock
    from app.models.work import Work
    from datetime import datetime, timezone
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    db.add(MockExamLock(user_id=user.id, subject="Рисунок", is_locked=True,
                        locked_at=datetime.now(timezone.utc)))
    db.commit()
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _mock_upload(client, [("photos", ("w.jpg", _JPG_BYTES, "image/jpeg"))], subject="Рисунок")
    assert resp.status_code == 200
    assert "ожидает" in resp.text or "разблокировки" in resp.text or "сдан" in resp.text
    assert db.query(Work).filter(Work.user_id == user.id).count() == 0


def test_mock_exam_locks_independent_per_subject(auth_client, db):
    """Locking Рисунок does not block Композиция."""
    from app.models.mock_exam_lock import MockExamLock
    from datetime import datetime, timezone
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    _create_active_ticket(db, user, "Композиция")
    db.add(MockExamLock(user_id=user.id, subject="Рисунок", is_locked=True,
                        locked_at=datetime.now(timezone.utc)))
    db.commit()
    # Locked subject should be rejected
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp_locked = _mock_upload(client, [("photos", ("a.jpg", _JPG_BYTES, "image/jpeg"))], subject="Рисунок")
    assert "ожидает" in resp_locked.text or "сдан" in resp_locked.text
    # Unlocked subject should succeed
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp_ok = _mock_upload(client, [("photos", ("b.jpg", _JPG_BYTES, "image/jpeg"))], subject="Композиция")
    assert resp_ok.status_code == 200
    kompozitsiya_lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == user.id, MockExamLock.subject == "Композиция",
    ).first()
    assert kompozitsiya_lock is not None and kompozitsiya_lock.is_locked is True


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


def test_student_can_submit_after_unlock(auth_client, db):
    """After a lock is cleared, the student can submit that subject again."""
    from app.models.mock_exam_lock import MockExamLock
    from app.models.work import Work
    from datetime import datetime, timezone
    client, user = auth_client
    _create_active_period(db, user, "mock_exam")
    _create_active_ticket(db, user, "Рисунок")
    # Create an already-unlocked lock record (curator previously unlocked it)
    db.add(MockExamLock(user_id=user.id, subject="Рисунок", is_locked=False,
                        locked_at=datetime.now(timezone.utc),
                        unlocked_at=datetime.now(timezone.utc)))
    db.commit()
    with patch(_MOCK_N8N, new_callable=AsyncMock, return_value=_OK_RESULT):
        resp = _mock_upload(client, [("photos", ("w.jpg", _JPG_BYTES, "image/jpeg"))], subject="Рисунок")
    assert resp.status_code == 200
    assert db.query(Work).filter(Work.user_id == user.id).count() == 1
    # Lock should be re-engaged after re-submission
    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == user.id, MockExamLock.subject == "Рисунок",
    ).first()
    assert lock.is_locked is True
