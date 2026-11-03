"""Recorded lab openings; one event per student/card in ten minutes."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.activity_event import StudentActivityEvent
from app.services.lab_access import catalog

EVENT_LAB_CARD_OPEN = "lab_card_open"
EVENT_LAB_PAGE_OPEN = "lab_page_open"
EVENT_LAB_MODEL_LOADED = "lab_model_loaded"
EVENT_LAB_MODEL_FAILED = "lab_model_failed"


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


def record_model_result(db: Session, *, user_id: int, card_id: str, loaded: bool) -> bool:
    if card_id not in catalog() or catalog()[card_id]["hidden"]:
        return False
    event_type = EVENT_LAB_MODEL_LOADED if loaded else EVENT_LAB_MODEL_FAILED
    now = datetime.now(timezone.utc)
    details = f"card:{card_id}"
    recent = db.query(StudentActivityEvent.id).filter(
        StudentActivityEvent.user_id == user_id,
        StudentActivityEvent.event_type == event_type,
        StudentActivityEvent.details == details,
        StudentActivityEvent.created_at >= now - timedelta(minutes=10),
    ).first()
    if recent:
        return False
    db.add(StudentActivityEvent(
        user_id=user_id, event_type=event_type, details=details, created_at=now,
    ))
    db.commit()
    return True


def record_page_open(db: Session, *, user_id: int) -> bool:
    now = datetime.now(timezone.utc)
    recent = db.query(StudentActivityEvent.id).filter(
        StudentActivityEvent.user_id == user_id,
        StudentActivityEvent.event_type == EVENT_LAB_PAGE_OPEN,
        StudentActivityEvent.created_at >= now - timedelta(minutes=10),
    ).first()
    if recent:
        return False
    db.add(StudentActivityEvent(user_id=user_id, event_type=EVENT_LAB_PAGE_OPEN, created_at=now))
    db.commit()
    return True


def model_result_stats(db: Session, *, days: int = 30) -> dict:
    from app.models.user import User

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(StudentActivityEvent).filter(
        StudentActivityEvent.event_type.in_((EVENT_LAB_MODEL_LOADED, EVENT_LAB_MODEL_FAILED)),
        StudentActivityEvent.created_at >= since,
    ).order_by(StudentActivityEvent.created_at.desc()).all()
    loaded = {row.user_id for row in rows if row.event_type == EVENT_LAB_MODEL_LOADED}
    failed = {row.user_id for row in rows if row.event_type == EVENT_LAB_MODEL_FAILED}
    visitors = {user_id for (user_id,) in db.query(StudentActivityEvent.user_id).filter(
        StudentActivityEvent.event_type == EVENT_LAB_PAGE_OPEN,
        StudentActivityEvent.created_at >= since,
    ).distinct()}
    latest_failures = {}
    for row in rows:
        if row.event_type != EVENT_LAB_MODEL_FAILED or row.user_id in latest_failures:
            continue
        latest_failures[row.user_id] = row
    names = dict(db.query(User.id, User.name).filter(User.id.in_(latest_failures)).all()) if latest_failures else {}
    return {
        "loaded_students": len(loaded),
        "failed_students": len(failed),
        "attempted_students": len(loaded | failed),
        "visited_students": len(visitors),
        "no_result_students": len(visitors - loaded - failed),
        "loaded_share": round(100 * len(visitors & loaded) / len(visitors)) if visitors else None,
        "failures": [
            {"name": names.get(user_id, str(user_id)),
             "card": card_label(row.details.removeprefix("card:")) if row.details else "",
             "at": row.created_at}
            for user_id, row in latest_failures.items()
        ],
    }


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
