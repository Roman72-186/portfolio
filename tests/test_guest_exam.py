"""Tests for the guest exam module (Track B) — temporary, isolated guest access.

Covers: config window gating, participant creation/resume by code, one-ticket-per-
subject idempotency, upload flow, staff scoring, and isolation from the main
User/Session/RBAC system.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.csrf import generate_csrf_token
from app.models.guest_exam import GuestExamConfig, GuestParticipant, GuestSubmission, GuestTicket
from app.services import guest_exam as guest_exam_service

_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # minimal JPEG header, same as test_routes_upload.py

GUEST_COOKIE_NAME = guest_exam_service.GUEST_COOKIE_NAME


@pytest.fixture()
def guest_config_factory(db):
    def _make(
        *,
        token: str = "trial-test",
        starts_hours: float = -1,
        ends_hours: float = 48,
        is_active: bool = True,
        title: str = "Пробный экзамен Apparchi",
    ) -> GuestExamConfig:
        # UTC, не now_msk(): DateTime(timezone=True) в SQLite при перечитывании
        # теряет исходную зону и переприкрепляется как UTC (см. conftest.py
        # _attach_utc) — конструирование в MSK внесло бы фантомный сдвиг в 3 часа
        # на малых интервалах (часы), как здесь.
        now = datetime.now(timezone.utc)
        config = GuestExamConfig(
            token=token,
            title=title,
            starts_at=now + timedelta(hours=starts_hours),
            ends_at=now + timedelta(hours=ends_hours),
            is_active=is_active,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
        return config
    return _make


@pytest.fixture()
def guest_ticket_factory(db):
    def _make(config: GuestExamConfig, *, subject: str = "Рисунок", title: str = "Натюрморт", is_active: bool = True) -> GuestTicket:
        ticket = GuestTicket(config_id=config.id, subject=subject, title=title, is_active=is_active)
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        return ticket
    return _make


def _login_guest(client, db, config: GuestExamConfig, *, name: str = "Гость") -> GuestParticipant:
    """Create a participant directly via the service layer and attach the guest
    cookie to the test client — bypasses the rate-limited /start route for tests
    that don't specifically exercise it."""
    participant = guest_exam_service.create_participant(db, config, name)
    cookie_value = guest_exam_service.dump_guest_cookie(participant.id, config.token)
    client.cookies.set(GUEST_COOKIE_NAME, cookie_value)
    return participant


def _guest_csrf(client) -> str:
    raw = client.cookies.get(GUEST_COOKIE_NAME, "")
    return generate_csrf_token(raw)


# ---------------------------------------------------------------------------
# Window gating
# ---------------------------------------------------------------------------

def test_landing_404_for_unknown_token(client):
    resp = client.get("/guest/does-not-exist")
    assert resp.status_code == 404


def test_landing_shows_closed_before_window_opens(client, guest_config_factory):
    config = guest_config_factory(starts_hours=5, ends_hours=48)
    resp = client.get(f"/guest/{config.token}")
    assert resp.status_code == 200
    assert "Приём работ проходит с" in resp.text


def test_start_rejected_when_window_closed(client, guest_config_factory):
    config = guest_config_factory(starts_hours=-48, ends_hours=-1)
    resp = client.post(f"/guest/{config.token}/start", data={"display_name": "Аня"})
    assert resp.status_code == 403


def test_start_rejected_when_config_inactive(client, guest_config_factory):
    config = guest_config_factory(is_active=False)
    resp = client.post(f"/guest/{config.token}/start", data={"display_name": "Аня"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Participant creation / resume by code
# ---------------------------------------------------------------------------

def test_start_creates_participant_and_sets_cookie(client, guest_config_factory):
    config = guest_config_factory()
    resp = client.post(
        f"/guest/{config.token}/start",
        data={"display_name": "Аня"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/guest/{config.token}/exam"
    assert GUEST_COOKIE_NAME in resp.cookies


def test_start_rejects_empty_name(client, guest_config_factory):
    config = guest_config_factory()
    resp = client.post(f"/guest/{config.token}/start", data={"display_name": "   "})
    assert resp.status_code == 400


def test_participant_code_uses_safe_alphabet(db, guest_config_factory):
    config = guest_config_factory()
    participant = guest_exam_service.create_participant(db, config, "Тест")
    assert len(participant.participant_code) == 8
    for forbidden in "0O1I":
        assert forbidden not in participant.participant_code


def test_resume_by_code_from_a_different_client(client, db, guest_config_factory):
    from fastapi.testclient import TestClient
    from app.main import app

    config = guest_config_factory()
    participant = guest_exam_service.create_participant(db, config, "Борис")

    with TestClient(app, base_url="https://testserver") as other_client:
        other_client.cookies.clear()
        resp = other_client.post(
            f"/guest/{config.token}/start",
            data={"code": participant.participant_code},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert GUEST_COOKIE_NAME in resp.cookies


def test_resume_with_wrong_code_shows_error(client, guest_config_factory):
    config = guest_config_factory()
    resp = client.post(f"/guest/{config.token}/start", data={"code": "ZZZZZZZZ"})
    assert resp.status_code == 400


def test_landing_redirects_logged_in_guest_straight_to_exam(client, db, guest_config_factory):
    config = guest_config_factory()
    _login_guest(client, db, config)
    resp = client.get(f"/guest/{config.token}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/guest/{config.token}/exam"


# ---------------------------------------------------------------------------
# Ticket issuance — one per subject, idempotent
# ---------------------------------------------------------------------------

def test_ticket_issuance_is_idempotent_per_subject(client, db, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок", title="Билет A")
    participant = _login_guest(client, db, config)

    csrf = _guest_csrf(client)
    r1 = client.post(
        f"/guest/{config.token}/exam/Рисунок/ticket",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r1.status_code == 303
    r2 = client.post(
        f"/guest/{config.token}/exam/Рисунок/ticket",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r2.status_code == 303

    count = (
        db.query(GuestSubmission)
        .filter(GuestSubmission.participant_id == participant.id, GuestSubmission.subject == "Рисунок")
        .count()
    )
    assert count == 1


def test_ticket_issuance_without_csrf_fails(client, db, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    _login_guest(client, db, config)

    resp = client.post(f"/guest/{config.token}/exam/Рисунок/ticket", data={"csrf_token": "wrong"})
    assert resp.status_code == 403


def test_ticket_issuance_without_active_tickets_returns_409(client, db, guest_config_factory):
    config = guest_config_factory()  # no tickets seeded
    _login_guest(client, db, config)
    csrf = _guest_csrf(client)
    resp = client.post(f"/guest/{config.token}/exam/Композиция/ticket", data={"csrf_token": csrf})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Upload flow
# ---------------------------------------------------------------------------

def test_upload_without_ticket_returns_400(client, db, guest_config_factory):
    config = guest_config_factory()
    _login_guest(client, db, config)
    csrf = _guest_csrf(client)
    resp = client.post(
        f"/guest/{config.token}/exam/Рисунок/upload",
        data={"csrf_token": csrf},
        files={"photo": ("photo.jpg", _JPG_BYTES, "image/jpeg")},
    )
    assert resp.status_code == 400


def test_upload_without_cookie_returns_401(client, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    resp = client.post(
        f"/guest/{config.token}/exam/Рисунок/upload",
        data={"csrf_token": "irrelevant"},
        files={"photo": ("photo.jpg", _JPG_BYTES, "image/jpeg")},
    )
    assert resp.status_code in (401, 403)


def test_upload_success_marks_submission_submitted(client, db, guest_config_factory, guest_ticket_factory, monkeypatch):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = _login_guest(client, db, config)
    csrf = _guest_csrf(client)

    client.post(f"/guest/{config.token}/exam/Рисунок/ticket", data={"csrf_token": csrf})

    monkeypatch.setattr(
        "app.api.guest_exam.s3_service.upload_to_s3",
        lambda path, data, content_type="image/jpeg": "https://s3.example/guest-exam/fake.jpg",
    )

    resp = client.post(
        f"/guest/{config.token}/exam/Рисунок/upload",
        data={"csrf_token": csrf},
        files={"photo": ("photo.jpg", _JPG_BYTES, "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    submission = guest_exam_service.get_submission(db, participant.id, "Рисунок")
    assert submission.status == "submitted"
    assert submission.s3_url == "https://s3.example/guest-exam/fake.jpg"


# ---------------------------------------------------------------------------
# Staff scoring
# ---------------------------------------------------------------------------

def test_score_requires_curator_role(client, db, guest_config_factory, guest_ticket_factory, auth_client):
    """`auth_client` (see conftest.py) is a plain student session — must not be
    able to score guest submissions."""
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    student_client, _ = auth_client
    resp = student_client.post(
        f"/cabinet/staff/guest-exam/{submission.id}/score",
        data={"score": "80", "comment": "Хорошо"},
    )
    assert resp.status_code == 403


def test_score_flow_by_curator(client, db, guest_config_factory, guest_ticket_factory, user_factory, session_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    curator = user_factory(vk_id=555_555, name="Куратор", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)

    resp = client.post(
        f"/cabinet/staff/guest-exam/{submission.id}/score",
        data={"score": "85.5", "comment": "Отличная работа"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db.refresh(submission)
    assert submission.status == "scored"
    assert float(submission.score) == 85.5
    assert submission.comment == "Отличная работа"
    assert submission.scored_by_id == curator.id


# ---------------------------------------------------------------------------
# Isolation from the main auth/session system
# ---------------------------------------------------------------------------

def test_guest_routes_ignore_real_session_cookie(client, db, guest_config_factory, auth_client):
    """A logged-in real student hitting a guest link gets exactly the same guest
    landing page as an anonymous visitor — the real session_id cookie must not
    grant any special guest access or bypass the guest flow."""
    config = guest_config_factory()
    student_client, _ = auth_client

    anon_resp = client.get(f"/guest/{config.token}")
    student_resp = student_client.get(f"/guest/{config.token}")

    assert anon_resp.status_code == student_resp.status_code == 200
    assert "Ваше имя" in anon_resp.text
    assert "Ваше имя" in student_resp.text


def test_guest_module_does_not_touch_core_tables(client, db, guest_config_factory, guest_ticket_factory):
    """Running a full guest flow must not create/mutate rows in users/sessions."""
    from app.models.user import User
    from app.models.session import Session as DbSession

    users_before = db.query(User).count()
    sessions_before = db.query(DbSession).count()

    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    _login_guest(client, db, config)
    csrf = _guest_csrf(client)
    client.post(f"/guest/{config.token}/exam/Рисунок/ticket", data={"csrf_token": csrf})

    assert db.query(User).count() == users_before
    assert db.query(DbSession).count() == sessions_before
