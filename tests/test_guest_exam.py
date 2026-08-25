"""Tests for the guest mode module (Track B) — temporary, isolated guest access.

Covers: link on/off gating (no date window — indefinite link), visit logging,
participant creation/resume by code, one-ticket-per-subject idempotency, upload
flow, admin-only ticket/link/scoring management, and isolation from the main
User/Session/RBAC system.
"""
import pytest

from app.csrf import generate_csrf_token
from app.models.exam_assignment import ExamTicket
from app.models.guest_exam import (
    GuestExamConfig,
    GuestParticipant,
    GuestSubmission,
    GuestVisit,
)
from app.services import guest_exam as guest_exam_service

_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # minimal JPEG header, same as test_routes_upload.py

GUEST_COOKIE_NAME = guest_exam_service.GUEST_COOKIE_NAME


@pytest.fixture()
def guest_config_factory(db):
    def _make(
        *,
        token: str = "trial-test",
        is_active: bool = True,
        title: str = "Пробный экзамен Apparchi",
    ) -> GuestExamConfig:
        config = GuestExamConfig(token=token, title=title, is_active=is_active)
        db.add(config)
        db.commit()
        db.refresh(config)
        return config
    return _make


@pytest.fixture()
def guest_ticket_factory(db, user_factory):
    """Билет гостевого режима — теперь настоящий ExamTicket(kind="guest"),
    создаётся тем же сервисом, что и в проде (guest_exam_service.create_guest_ticket).
    `config` больше не участвует в связи (билеты общие по subject, не привязаны
    к конкретной ссылке) — параметр оставлен, чтобы не переписывать вызовы."""
    _creator_id: dict[str, int] = {}

    def _make(config: GuestExamConfig, *, subject: str = "Рисунок", title: str = "Натюрморт") -> ExamTicket:
        if "id" not in _creator_id:
            creator = user_factory(vk_id=900_001, name="Guest Ticket Creator", role_name="админ")
            _creator_id["id"] = creator.id
        return guest_exam_service.create_guest_ticket(
            db,
            subject=subject,
            title=title,
            description=None,
            image_url=None,
            image_path=None,
            created_by_id=_creator_id["id"],
        )
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
# Link on/off gating — no date window, just is_active
# ---------------------------------------------------------------------------

def test_landing_404_for_unknown_token(client):
    resp = client.get("/guest/does-not-exist")
    assert resp.status_code == 404


def test_landing_shows_closed_when_link_inactive(client, guest_config_factory):
    config = guest_config_factory(is_active=False)
    resp = client.get(f"/guest/{config.token}")
    assert resp.status_code == 200
    assert "закрыт" in resp.text


def test_start_rejected_when_config_inactive(client, guest_config_factory):
    config = guest_config_factory(is_active=False)
    resp = client.post(f"/guest/{config.token}/start", data={"display_name": "Аня"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Visit logging
# ---------------------------------------------------------------------------

def test_landing_visit_is_logged_anonymous(client, db, guest_config_factory):
    config = guest_config_factory()
    client.get(f"/guest/{config.token}")
    visit = db.query(GuestVisit).filter(GuestVisit.config_id == config.id).first()
    assert visit is not None
    assert visit.participant_id is None


def test_landing_visit_is_logged_with_participant(client, db, guest_config_factory):
    config = guest_config_factory()
    participant = _login_guest(client, db, config)
    client.get(f"/guest/{config.token}", follow_redirects=False)
    visit = (
        db.query(GuestVisit)
        .filter(GuestVisit.config_id == config.id, GuestVisit.participant_id == participant.id)
        .first()
    )
    assert visit is not None


def test_config_stats_counts_visits_participants_submissions(db, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    guest_exam_service.record_visit(db, config.id, participant.id)
    guest_exam_service.record_visit(db, config.id, participant.id)
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    stats = guest_exam_service.config_stats(db, config.id)
    assert stats["visits"] == 2
    assert stats["participants"] == 1
    assert stats["submitted"] == 1


def test_get_primary_config_prefers_active_over_newer_inactive(db, guest_config_factory):
    old_active = guest_config_factory(token="old-active", is_active=True)
    guest_config_factory(token="new-inactive", is_active=False)
    primary = guest_exam_service.get_primary_config(db)
    assert primary.id == old_active.id


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
# Exam page — «Получить билет» disabled per subject without tickets
# ---------------------------------------------------------------------------

def test_exam_page_hides_ticket_button_for_subject_without_tickets(
    client, db, guest_config_factory, guest_ticket_factory
):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")  # «Композиция» остаётся без билетов
    _login_guest(client, db, config)

    resp = client.get(f"/guest/{config.token}/exam")
    assert resp.status_code == 200
    assert "Билеты по этому предмету пока не готовы" in resp.text
    # у «Рисунка» билет есть — кнопка должна остаться активной
    assert resp.text.count('action="/guest/{}/exam/Рисунок/ticket"'.format(config.token)) == 1


def test_exam_page_shows_ticket_button_when_tickets_exist(
    client, db, guest_config_factory, guest_ticket_factory
):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    guest_ticket_factory(config, subject="Композиция")
    _login_guest(client, db, config)

    resp = client.get(f"/guest/{config.token}/exam")
    assert resp.status_code == 200
    assert "Билеты по этому предмету пока не готовы" not in resp.text


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
# Второй билет открывается только после загрузки работы по первому
# ---------------------------------------------------------------------------

def test_second_subject_ticket_locked_until_first_uploaded(db, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    guest_ticket_factory(config, subject="Композиция")
    participant = guest_exam_service.create_participant(db, config, "Гость")

    guest_exam_service.issue_ticket(db, participant, "Рисунок")
    with pytest.raises(guest_exam_service.TicketLockedError) as exc:
        guest_exam_service.issue_ticket(db, participant, "Композиция")
    assert exc.value.subject == "Рисунок"


def test_second_subject_ticket_unlocks_after_upload(db, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    guest_ticket_factory(config, subject="Композиция")
    participant = guest_exam_service.create_participant(db, config, "Гость")

    first = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, first, "https://s3.example/r.jpg", "guest-exam/r.jpg")

    second = guest_exam_service.issue_ticket(db, participant, "Композиция")
    assert second.status == "issued"


def test_reissue_of_same_subject_is_not_blocked_by_own_pending_ticket(
    db, guest_config_factory, guest_ticket_factory
):
    """Гейт не должен блокировать сам себя: повторный клик по уже выданному
    предмету по-прежнему возвращает тот же билет."""
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")

    first = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    again = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    assert again.id == first.id


def test_exam_page_replaces_second_ticket_button_with_lock_message(
    client, db, guest_config_factory, guest_ticket_factory
):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    guest_ticket_factory(config, subject="Композиция")
    participant = _login_guest(client, db, config)
    guest_exam_service.issue_ticket(db, participant, "Рисунок")

    resp = client.get(f"/guest/{config.token}/exam")
    assert resp.status_code == 200
    assert "Сначала загрузите работу по предмету" in resp.text
    assert 'action="/guest/{}/exam/Композиция/ticket"'.format(config.token) not in resp.text


def test_locked_second_ticket_post_redirects_without_creating_submission(
    client, db, guest_config_factory, guest_ticket_factory
):
    """Устаревшая вторая вкладка: POST по закрытому предмету не создаёт сдачу,
    а возвращает гостя на страницу в актуальном состоянии."""
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    guest_ticket_factory(config, subject="Композиция")
    participant = _login_guest(client, db, config)
    guest_exam_service.issue_ticket(db, participant, "Рисунок")

    csrf = _guest_csrf(client)
    resp = client.post(
        f"/guest/{config.token}/exam/Композиция/ticket",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert (
        db.query(GuestSubmission)
        .filter(
            GuestSubmission.participant_id == participant.id,
            GuestSubmission.subject == "Композиция",
        )
        .count()
        == 0
    )


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


def test_upload_rejected_when_link_disabled_mid_session(client, db, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    _login_guest(client, db, config)
    csrf = _guest_csrf(client)
    client.post(f"/guest/{config.token}/exam/Рисунок/ticket", data={"csrf_token": csrf})

    config.is_active = False
    db.commit()

    resp = client.post(
        f"/guest/{config.token}/exam/Рисунок/upload",
        data={"csrf_token": csrf},
        files={"photo": ("photo.jpg", _JPG_BYTES, "image/jpeg")},
    )
    assert resp.status_code == 403


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

    # Билет заводит staff-админ (настоящий User) — это происходит до входа гостя
    # и не в счёт: тест проверяет, что именно ДЕЙСТВИЯ ГОСТЯ (вход, получение
    # билета) не трогают users/sessions, а не что таблица User вообще пуста.
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")

    users_before = db.query(User).count()
    sessions_before = db.query(DbSession).count()

    _login_guest(client, db, config)
    csrf = _guest_csrf(client)
    client.post(f"/guest/{config.token}/exam/Рисунок/ticket", data={"csrf_token": csrf})

    assert db.query(User).count() == users_before
    assert db.query(DbSession).count() == sessions_before


# ---------------------------------------------------------------------------
# Admin: единая страница «Гостевой режим» (rank >= 4, не curator) — билеты через
# ту же логику, что реальные билеты пробника (форма + /cabinet/upload-ticket-image)
# ---------------------------------------------------------------------------

def test_guest_mode_page_requires_admin_rank(client, user_factory, session_factory):
    curator = user_factory(vk_id=444_444, name="Куратор", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/staff/guest-exam")
    assert resp.status_code == 403


def test_guest_mode_page_renders_tabs_for_admin(admin_client):
    admin_ui_client, _ = admin_client
    for tab in ("tickets", "link", "works"):
        resp = admin_ui_client.get(f"/cabinet/staff/guest-exam?tab={tab}")
        assert resp.status_code == 200


def test_link_create_and_appears_as_primary(admin_client, db):
    admin_ui_client, admin = admin_client
    resp = admin_ui_client.post(
        "/cabinet/staff/guest-exam/link",
        data={"token": "trial-e2e", "title": "Пробник E2E"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    config = db.query(GuestExamConfig).filter(GuestExamConfig.token == "trial-e2e").first()
    assert config is not None
    assert config.created_by_id == admin.id

    detail = admin_ui_client.get("/cabinet/staff/guest-exam?tab=link")
    assert detail.status_code == 200
    assert "Пробник E2E" in detail.text


def test_link_create_rejects_duplicate_token(admin_client, guest_config_factory):
    admin_ui_client, _ = admin_client
    guest_config_factory(token="dup-token")
    resp = admin_ui_client.post(
        "/cabinet/staff/guest-exam/link",
        data={"token": "dup-token", "title": "Ещё одна"},
    )
    assert resp.status_code == 422


def test_link_toggle_flips_active(admin_client, db, guest_config_factory):
    admin_ui_client, _ = admin_client
    config = guest_config_factory(is_active=True)
    resp = admin_ui_client.post(
        f"/cabinet/staff/guest-exam/link/{config.id}/toggle",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(config)
    assert config.is_active is False


def test_ticket_create_via_admin_form_is_usable_by_guest(admin_client, db, guest_config_factory):
    """Билет, добавленный через форму «Билеты» (та же логика, что и у реальных
    билетов пробника: поля + фото через /cabinet/upload-ticket-image), пишется в
    настоящую ExamTicket (kind="guest") и реально попадает в пул, выдаваемый
    гостю текущей (активной) ссылки."""
    from fastapi.testclient import TestClient
    from app.main import app

    admin_ui_client, _ = admin_client
    config = guest_config_factory()

    resp = admin_ui_client.post(
        "/cabinet/staff/guest-exam/tickets",
        data={
            "subject": "Рисунок",
            "title": "Натюрморт",
            "description": "Нарисуйте натюрморт из трёх предметов",
            "image_url": "https://s3.example/ticket.jpg",
            "image_path": "guest-exam/tickets/ticket.jpg",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    tickets = guest_exam_service.list_guest_tickets(db)
    ticket = next((t for t in tickets if t.title == "Натюрморт"), None)
    assert ticket is not None
    assert ticket.image_s3_url == "https://s3.example/ticket.jpg"
    assert ticket.subject == "Рисунок"

    with TestClient(app, base_url="https://testserver") as guest_client:
        participant = _login_guest(guest_client, db, config)
        csrf = _guest_csrf(guest_client)
        ticket_resp = guest_client.post(
            f"/guest/{config.token}/exam/Рисунок/ticket",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert ticket_resp.status_code == 303
        submission = guest_exam_service.get_submission(db, participant.id, "Рисунок")
        assert submission.ticket_title == "Натюрморт"


def test_ticket_create_without_any_link_returns_422(admin_client):
    admin_ui_client, _ = admin_client
    resp = admin_ui_client.post(
        "/cabinet/staff/guest-exam/tickets",
        data={"subject": "Рисунок", "title": "Натюрморт"},
    )
    assert resp.status_code == 422


def test_ticket_delete_removes_it_from_pool(admin_client, db, guest_config_factory, guest_ticket_factory):
    admin_ui_client, _ = admin_client
    config = guest_config_factory()
    ticket = guest_ticket_factory(config, subject="Рисунок")

    resp = admin_ui_client.post(
        f"/cabinet/staff/guest-exam/tickets/{ticket.id}/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert db.query(ExamTicket).filter(ExamTicket.id == ticket.id).first() is None


def test_ticket_delete_ignores_real_exam_ticket(admin_client, db):
    """Страховка от риска, из-за которого билеты были в отдельной таблице:
    удаление по id не должно задевать билет реального пробника (kind="mock")."""
    from datetime import date
    from app.models.exam_assignment import ExamAssignment

    admin_ui_client, admin = admin_client
    real_assignment = ExamAssignment(
        title="Реальный пробник", subject="Рисунок", kind="mock",
        created_by_id=admin.id, status="published",
    )
    db.add(real_assignment)
    db.commit()
    db.refresh(real_assignment)
    real_ticket = ExamTicket(
        assignment_id=real_assignment.id, ticket_number=1, title="Настоящий билет",
        start_date=date.today(), end_date=date.today(),
        assign_to_all=True,
    )
    db.add(real_ticket)
    db.commit()
    db.refresh(real_ticket)

    resp = admin_ui_client.post(f"/cabinet/staff/guest-exam/tickets/{real_ticket.id}/delete")
    assert resp.status_code == 404
    assert db.query(ExamTicket).filter(ExamTicket.id == real_ticket.id).first() is not None


def test_score_requires_admin_rank_not_curator(client, db, guest_config_factory, guest_ticket_factory, user_factory, session_factory):
    """Проверка сдач теперь тоже rank >= 4 — куратор больше не допускается."""
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    curator = user_factory(vk_id=555_555, name="Куратор", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)

    resp = client.post(
        f"/cabinet/staff/guest-exam/works/{submission.id}/score",
        data={"score": "80", "comment": "Хорошо"},
    )
    assert resp.status_code == 403


def test_score_flow_by_admin(admin_client, db, guest_config_factory, guest_ticket_factory):
    admin_ui_client, admin = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    resp = admin_ui_client.post(
        f"/cabinet/staff/guest-exam/works/{submission.id}/score",
        data={"score": "85.5", "comment": "Отличная работа"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cabinet/staff/guest-exam?tab=works"

    db.refresh(submission)
    assert submission.status == "scored"
    assert float(submission.score) == 85.5
    assert submission.comment == "Отличная работа"
    assert submission.scored_by_id == admin.id


# ---------------------------------------------------------------------------
# Билеты гостевого режима — теперь настоящие ExamTicket/ExamAssignment(kind=
# "guest"), переиспользуют ту же таблицу, что реальный пробник. Риск, из-за
# которого раньше была отдельная таблица GuestTicket: gостевой билет мог бы
# протечь настоящему ученику через резолверы, которые не фильтруют по kind.
# ---------------------------------------------------------------------------

def test_guest_tickets_do_not_leak_into_real_student_resolver(db, guest_config_factory, guest_ticket_factory, user_factory):
    from app.services.exam_cycle import get_active_tickets

    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок", title="Гостевой билет")
    student = user_factory(vk_id=700_001, name="Реальный ученик", role_name="ученик")

    tickets = get_active_tickets(db, student.id, "Рисунок")
    assert all(t.title != "Гостевой билет" for t in tickets)


def test_guest_assignments_excluded_from_admin_exam_hub_counts(admin_client, db, guest_config_factory, guest_ticket_factory):
    """exam_assignments_hub считает published/draft для реальных заданий — гостевые
    (kind="guest") не должны туда попадать и путать админа с реальными пробниками."""
    admin_ui_client, _ = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")

    resp = admin_ui_client.get("/cabinet/exam-assignments")
    assert resp.status_code == 200

    active_list_resp = admin_ui_client.get("/cabinet/exam-assignments/active")
    assert "Гостевой режим — Рисунок" not in active_list_resp.text


# ---------------------------------------------------------------------------
# Вкладка «Работы» на /cabinet/staff/guest-exam — один ряд на участника (имя +
# дата сдачи), раскрытие по предмету (Рисунок/Композиция), оценка с
# опциональным текстовым комментарием или фото обратной связи.
# ---------------------------------------------------------------------------

def test_works_tab_lists_participant_with_submission(admin_client, db, guest_config_factory, guest_ticket_factory):
    admin_ui_client, _ = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость на проверке")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    resp = admin_ui_client.get("/cabinet/staff/guest-exam?tab=works")
    assert resp.status_code == 200
    assert "Гость на проверке" in resp.text


def test_works_tab_hides_participant_without_submission(admin_client, db, guest_config_factory):
    admin_ui_client, _ = admin_client
    config = guest_config_factory()
    guest_exam_service.create_participant(db, config, "Ещё не сдавший")

    resp = admin_ui_client.get("/cabinet/staff/guest-exam?tab=works")
    assert resp.status_code == 200
    assert "Ещё не сдавший" not in resp.text


def test_participants_board_groups_two_subjects_under_one_participant(db, guest_config_factory, guest_ticket_factory):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок", title="Билет Р")
    guest_ticket_factory(config, subject="Композиция", title="Билет К")
    participant = guest_exam_service.create_participant(db, config, "Гость по двум предметам")

    sub_r = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, sub_r, "https://s3.example/r.jpg", "guest-exam/r.jpg")
    sub_k = guest_exam_service.issue_ticket(db, participant, "Композиция")
    guest_exam_service.record_upload(db, sub_k, "https://s3.example/k.jpg", "guest-exam/k.jpg")

    board = guest_exam_service.list_participants_board(db)
    entry = next(e for e in board if e["participant"].id == participant.id)
    assert set(entry["subs"].keys()) == {"Рисунок", "Композиция"}


def test_score_flow_saves_feedback_image_and_redirects_to_works_tab(admin_client, db, guest_config_factory, guest_ticket_factory):
    admin_ui_client, admin = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    resp = admin_ui_client.post(
        f"/cabinet/staff/guest-exam/works/{submission.id}/score",
        data={
            "score": "90",
            "comment": "",
            "feedback_image_url": "https://s3.example/feedback.jpg",
            "feedback_image_path": "guest-exam/feedback/feedback.jpg",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cabinet/staff/guest-exam?tab=works"

    db.refresh(submission)
    assert submission.status == "scored"
    assert submission.feedback_image_url == "https://s3.example/feedback.jpg"
    assert submission.feedback_image_path == "guest-exam/feedback/feedback.jpg"
    assert submission.comment is None

# ---------------------------------------------------------------------------
# Вкладка «Участники» — удаление тестовых прохождений, только суперадмин
# ---------------------------------------------------------------------------

def test_participants_tab_hidden_from_admin_rank_four(
    client, db, guest_config_factory, user_factory, session_factory
):
    """Ранг 4 не должен попасть на вкладку даже вручную набранным ?tab=
    (фикстура admin_client — суперадмин, поэтому ранг 4 заводим явно)."""
    config = guest_config_factory()
    guest_exam_service.create_participant(db, config, "Гость без сдач")

    admin = user_factory(vk_id=910_004, name="Главный преподаватель", role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)

    resp = client.get("/cabinet/staff/guest-exam?tab=participants")
    assert resp.status_code == 200
    assert "Гость без сдач" not in resp.text


def test_participants_tab_lists_everyone_for_superadmin(
    client, db, guest_config_factory, user_factory, session_factory
):
    """В отличие от «Работ», здесь видны и те, кто ничего не сдал."""
    config = guest_config_factory()
    guest_exam_service.create_participant(db, config, "Гость без сдач")

    superadmin = user_factory(vk_id=910_001, name="Суперадмин", role_name="суперадмин")
    client.cookies.set("session_id", session_factory(superadmin).id)

    resp = client.get("/cabinet/staff/guest-exam?tab=participants")
    assert resp.status_code == 200
    assert "Гость без сдач" in resp.text


def test_delete_participant_requires_superadmin(
    client, db, guest_config_factory, guest_ticket_factory, user_factory, session_factory
):
    """Удаление — только ранг 5: главный преподаватель (4) получает 403."""
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    guest_exam_service.issue_ticket(db, participant, "Рисунок")

    admin = user_factory(vk_id=910_005, name="Главный преподаватель", role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)

    resp = client.post(f"/cabinet/staff/guest-exam/participants/{participant.id}/delete")
    assert resp.status_code == 403
    assert db.query(GuestParticipant).filter(GuestParticipant.id == participant.id).first() is not None


def test_superadmin_deletes_participant_with_submissions_and_visits(
    client, db, guest_config_factory, guest_ticket_factory, user_factory, session_factory
):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Тестовое прохождение")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")
    guest_exam_service.record_visit(db, config.id, participant.id)

    superadmin = user_factory(vk_id=910_002, name="Суперадмин", role_name="суперадмин")
    client.cookies.set("session_id", session_factory(superadmin).id)

    resp = client.post(
        f"/cabinet/staff/guest-exam/participants/{participant.id}/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cabinet/staff/guest-exam?tab=participants"

    assert db.query(GuestParticipant).filter(GuestParticipant.id == participant.id).first() is None
    assert (
        db.query(GuestSubmission).filter(GuestSubmission.participant_id == participant.id).count() == 0
    )
    # Визит остаётся как обезличенная строка статистики — счётчик заходов не проседает.
    visit = db.query(GuestVisit).filter(GuestVisit.config_id == config.id).first()
    assert visit is not None and visit.participant_id is None


def test_delete_participant_keeps_shared_ticket_image(
    client, db, guest_config_factory, guest_ticket_factory, user_factory, session_factory
):
    """Картинка билета общая — удаление участника не должно её задевать."""
    config = guest_config_factory()
    ticket = guest_ticket_factory(config, subject="Рисунок")
    ticket.image_s3_path = "guest-exam/tickets/shared.jpg"
    db.commit()

    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/w.jpg", "guest-exam/w.jpg")

    _, s3_paths = guest_exam_service.delete_participant(db, participant.id)
    assert "guest-exam/w.jpg" in s3_paths
    assert "guest-exam/tickets/shared.jpg" not in s3_paths
    assert db.query(ExamTicket).filter(ExamTicket.id == ticket.id).first() is not None


def test_delete_unknown_participant_returns_404(
    client, db, user_factory, session_factory
):
    superadmin = user_factory(vk_id=910_003, name="Суперадмин", role_name="суперадмин")
    client.cookies.set("session_id", session_factory(superadmin).id)

    resp = client.post("/cabinet/staff/guest-exam/participants/999999/delete")
    assert resp.status_code == 404

# ---------------------------------------------------------------------------
# Отмена загрузки — проверяющий возвращает работу гостю на перезагрузку
# ---------------------------------------------------------------------------

def test_cancel_upload_returns_submission_to_issued(
    admin_client, db, guest_config_factory, guest_ticket_factory
):
    admin_ui_client, _ = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок", title="Натюрморт")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    resp = admin_ui_client.post(
        f"/cabinet/staff/guest-exam/works/{submission.id}/cancel-upload",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cabinet/staff/guest-exam?tab=works"

    db.refresh(submission)
    assert submission.status == "issued"
    assert submission.s3_url is None
    assert submission.s3_path is None
    assert submission.submitted_at is None
    # Билет остаётся за участником — работу присылают по тому же заданию.
    assert submission.ticket_title == "Натюрморт"


def test_guest_can_upload_again_after_cancel(
    client, admin_client, db, guest_config_factory, guest_ticket_factory
):
    """Ради этого всё и делается: после отмены гость снова видит форму и грузит."""
    admin_ui_client, _ = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = _login_guest(client, db, config)
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    admin_ui_client.post(f"/cabinet/staff/guest-exam/works/{submission.id}/cancel-upload")

    client.cookies.set(GUEST_COOKIE_NAME, guest_exam_service.dump_guest_cookie(participant.id, config.token))
    page = client.get(f"/guest/{config.token}/exam")
    assert f'action="/guest/{config.token}/exam/Рисунок/upload"' in page.text

    resp = client.post(
        f"/guest/{config.token}/exam/Рисунок/upload",
        data={"csrf_token": _guest_csrf(client)},
        files={"photo": ("photo.jpg", _JPG_BYTES, "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(submission)
    assert submission.status == "submitted"


def test_cancel_upload_rejected_for_scored_work(
    admin_client, db, guest_config_factory, guest_ticket_factory
):
    admin_ui_client, admin = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")
    guest_exam_service.score_submission(
        db, submission, score=80, comment="Хорошо", scored_by_id=admin.id
    )

    resp = admin_ui_client.post(f"/cabinet/staff/guest-exam/works/{submission.id}/cancel-upload")
    assert resp.status_code == 400

    db.refresh(submission)
    assert submission.status == "scored"
    assert submission.s3_url == "https://s3.example/x.jpg"


def test_cancel_upload_rejected_when_nothing_uploaded(
    admin_client, db, guest_config_factory, guest_ticket_factory
):
    admin_ui_client, _ = admin_client
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")

    resp = admin_ui_client.post(f"/cabinet/staff/guest-exam/works/{submission.id}/cancel-upload")
    assert resp.status_code == 400


def test_cancel_upload_requires_admin_rank_not_curator(
    client, db, guest_config_factory, guest_ticket_factory, user_factory, session_factory
):
    config = guest_config_factory()
    guest_ticket_factory(config, subject="Рисунок")
    participant = guest_exam_service.create_participant(db, config, "Гость")
    submission = guest_exam_service.issue_ticket(db, participant, "Рисунок")
    guest_exam_service.record_upload(db, submission, "https://s3.example/x.jpg", "guest-exam/x.jpg")

    curator = user_factory(vk_id=910_006, name="Куратор", role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)

    resp = client.post(f"/cabinet/staff/guest-exam/works/{submission.id}/cancel-upload")
    assert resp.status_code == 403
    db.refresh(submission)
    assert submission.status == "submitted"
