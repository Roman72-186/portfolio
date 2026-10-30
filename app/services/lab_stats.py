"""Recorded lab openings; one event per student/card in ten minutes."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.activity_event import StudentActivityEvent
from app.services.lab_access import catalog

EVENT_LAB_CARD_OPEN = "lab_card_open"


def card_label(card_id: str) -> str:
    return catalog().get(card_id, {}).get("path", card_id)


def record_open(db: Session, *, user_id: int, card_id: str) -> bool:
    if card_id not in catalog() or catalog()[card_id]["hidden"]:
        return False
    now = datetime.now(timezone.utc)
    details = f"card:{card_id}"
    recent = db.query(StudentActivityEvent.id).filter(
        StudentActivityEvent.user_id == user_id,
        StudentActivityEvent.event_type == EVENT_LAB_CARD_OPEN,
        StudentActivityEvent.details == details,
        StudentActivityEvent.created_at >= now - timedelta(minutes=10),
    ).first()
    if recent:
        return False
    db.add(StudentActivityEvent(
        user_id=user_id, event_type=EVENT_LAB_CARD_OPEN,
        details=details, created_at=now,
    ))
    db.commit()
    return True


def top_cards(db: Session, *, days: int = 30) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(
        StudentActivityEvent.details,
        func.count(StudentActivityEvent.id),
        func.count(func.distinct(StudentActivityEvent.user_id)),
    ).filter(
        StudentActivityEvent.event_type == EVENT_LAB_CARD_OPEN,
        StudentActivityEvent.created_at >= since,
    ).group_by(StudentActivityEvent.details).order_by(func.count(StudentActivityEvent.id).desc()).all()
    return [
        {"card_id": details.removeprefix("card:"), "path": card_label(details.removeprefix("card:")),
         "opens": opens, "students": students}
        for details, opens, students in rows if details and details.startswith("card:")
    ]


def student_cards(db: Session, student_id: int) -> list[dict]:
    rows = db.query(StudentActivityEvent).filter(
        StudentActivityEvent.user_id == student_id,
        StudentActivityEvent.event_type == EVENT_LAB_CARD_OPEN,
    ).order_by(StudentActivityEvent.created_at.desc()).all()
    return [
        {"card_id": row.details.removeprefix("card:"),
         "path": card_label(row.details.removeprefix("card:")), "at": row.created_at}
        for row in rows if row.details and row.details.startswith("card:")
    ]
