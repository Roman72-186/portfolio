"""Бизнес-логика гостевого пробника — ВРЕМЕННЫЙ модуль, см. app/models/guest_exam.py.

Сессия гостя не проходит через app.dependencies/Session/User — отдельный подписанный
cookie (guest-v1), отдельный CSRF-контекст (переиспользует app.csrf с cookie-значением
вместо session_id, см. app/api/guest_exam.py::require_guest_csrf).
"""
import random
import secrets
from datetime import datetime, timezone

from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.models.guest_exam import (
    GuestExamConfig,
    GuestParticipant,
    GuestSubmission,
    GuestTicket,
)

GUEST_COOKIE_NAME = "guest_session"
# С запасом относительно окна 26-28.08 — участник может зайти посмотреть балл позже.
COOKIE_MAX_AGE = 30 * 24 * 3600

# Алфавит без спутываемых символов (0/O, 1/I).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
_CODE_GENERATION_ATTEMPTS = 10

# Только визуальный отсчёт на странице — не блокирует отправку после истечения
# (тот же принцип, что у реального пробника, см. mock_exam_access.py).
VISUAL_DURATION_MINUTES = 90


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="guest-v1")


def dump_guest_cookie(participant_id: int, config_token: str) -> str:
    return _serializer().dumps({"participant_id": participant_id, "config_token": config_token})


def load_guest_cookie(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        payload = _serializer().loads(raw, max_age=COOKIE_MAX_AGE)
    except BadData:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def get_config_by_token(db: DBSession, token: str) -> GuestExamConfig | None:
    return db.query(GuestExamConfig).filter(GuestExamConfig.token == token).first()


def get_participant(db: DBSession, participant_id: int | None, config_id: int) -> GuestParticipant | None:
    if not participant_id:
        return None
    return (
        db.query(GuestParticipant)
        .filter(GuestParticipant.id == participant_id, GuestParticipant.config_id == config_id)
        .first()
    )


def get_participant_by_code(db: DBSession, config_id: int, code: str) -> GuestParticipant | None:
    code_clean = (code or "").strip().upper()
    if not code_clean:
        return None
    return (
        db.query(GuestParticipant)
        .filter(
            GuestParticipant.config_id == config_id,
            GuestParticipant.participant_code == code_clean,
        )
        .first()
    )


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def create_participant(db: DBSession, config: GuestExamConfig, display_name: str) -> GuestParticipant:
    name_clean = (display_name or "").strip()[:200]
    if not name_clean:
        raise ValueError("empty_name")

    for _ in range(_CODE_GENERATION_ATTEMPTS):
        participant = GuestParticipant(
            config_id=config.id,
            display_name=name_clean,
            participant_code=_generate_code(),
        )
        db.add(participant)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(participant)
        return participant
    raise RuntimeError("Не удалось сгенерировать уникальный код участника")


def touch_participant(db: DBSession, participant: GuestParticipant) -> None:
    participant.last_seen_at = datetime.now(timezone.utc)
    db.commit()


def get_submission(db: DBSession, participant_id: int, subject: str) -> GuestSubmission | None:
    return (
        db.query(GuestSubmission)
        .filter(
            GuestSubmission.participant_id == participant_id,
            GuestSubmission.subject == subject,
        )
        .first()
    )


def issue_ticket(db: DBSession, participant: GuestParticipant, subject: str) -> GuestSubmission:
    """Выдать билет по предмету — идемпотентно, один билет на предмет на участника."""
    existing = get_submission(db, participant.id, subject)
    if existing:
        return existing

    tickets = (
        db.query(GuestTicket)
        .filter(
            GuestTicket.config_id == participant.config_id,
            GuestTicket.subject == subject,
            GuestTicket.is_active == True,  # noqa: E712
        )
        .all()
    )
    if not tickets:
        raise LookupError("no_active_tickets")
    ticket = random.choice(tickets)

    submission = GuestSubmission(
        participant_id=participant.id,
        subject=subject,
        ticket_id=ticket.id,
        ticket_title=ticket.title,
        ticket_description=ticket.description,
        ticket_image_url=ticket.image_s3_url,
        status="issued",
    )
    db.add(submission)
    try:
        db.commit()
    except IntegrityError:
        # Гонка (двойной клик/две вкладки) — билет по этому предмету уже выдан
        # параллельным запросом, возвращаем его вместо ошибки.
        db.rollback()
        existing = get_submission(db, participant.id, subject)
        if existing:
            return existing
        raise
    db.refresh(submission)
    return submission


def record_upload(
    db: DBSession, submission: GuestSubmission, s3_url: str | None, s3_path: str
) -> GuestSubmission:
    submission.s3_url = s3_url
    submission.s3_path = s3_path
    submission.submitted_at = datetime.now(timezone.utc)
    submission.status = "submitted"
    db.commit()
    db.refresh(submission)
    return submission


def score_submission(
    db: DBSession,
    submission: GuestSubmission,
    *,
    score,
    comment: str | None,
    scored_by_id: int,
) -> GuestSubmission:
    submission.score = score
    submission.comment = (comment or "").strip() or None
    submission.scored_by_id = scored_by_id
    submission.scored_at = datetime.now(timezone.utc)
    submission.status = "scored"
    db.commit()
    db.refresh(submission)
    return submission
