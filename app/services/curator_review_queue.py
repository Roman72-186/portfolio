"""Карточки и переходы проверки используют одну очередь куратора."""
from datetime import datetime
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.constants import MOCK_SUBJECTS
from app.models.user import User
from app.services.curator_demo import _period
from app.services.staff_dashboard import CURATOR_STATS_TARIFFS
from app.services.task_blocks import submission_review_queue
from app.services.tz import MSK_TZ

QUEUE_URL = "/cabinet/staff/students-review/queue"


def _feedback_url(submission_id: int, query: str) -> str:
    return f"/cabinet/staff/task-block-submissions/{submission_id}/feedback?{query}"


def build_review_queue(
    db: Session, curator_id: int, *, subject: str, tariff: str, cycle_id: int | None, status: str,
) -> dict:
    subject = subject if subject in MOCK_SUBJECTS else ""
    tariff = tariff if tariff in CURATOR_STATS_TARIFFS else ""
    status = "all" if status == "all" else ""
    period = _period(db, datetime.now(MSK_TZ).date(), None, cycle_id)
    cycle = period["cycle"]
    filters = {"subject": subject, "tariff": tariff, "status": status}
    if cycle:
        filters["cycle_id"] = cycle.id
    feedback_query = urlencode({f"review_{key}": value for key, value in filters.items()})
    cards = {}
    if subject and cycle:
        rows = submission_review_queue(
            db, curator_id=curator_id, subject=subject, tariff=tariff or None,
            cycle_id=cycle.id, limit=1000,
        )
        students = {
            student.id: student for student in db.query(User).filter(
                User.id.in_({row["student_id"] for row in rows}),
                User.curator_id == curator_id, User.is_active.is_(True), User.deleted_at.is_(None),
            )
        }
        # Одинаковое время сдачи не должно переставлять учеников при переходах.
        for row in sorted(rows, key=lambda item: (item["submitted_at"], item["submission_id"]), reverse=True):
            student_id = row["student_id"]
            if student_id not in students:
                continue
            card = cards.setdefault(student_id, {"student": students[student_id], "works": [], "work_ids": set()})
            card["work_ids"].add(row["submission_id"])
            if status != "all" and (row["reviewed"] or row["needs_revision"]):
                continue
            card["works"].append({**row, "url": _feedback_url(row["submission_id"], feedback_query)})
    return {
        "subject": subject, "subjects": MOCK_SUBJECTS, "tariff": tariff, "tariffs": CURATOR_STATS_TARIFFS,
        "period": period, "status": status, "cards": [card for card in cards.values() if card["works"]],
        "anchors": cards, "queue_url": QUEUE_URL + "?" + urlencode(filters),
    }


def review_neighbors(db: Session, curator_id: int, submission, query) -> dict | None:
    """Ведём по выбранной очереди. Проверенная работа остаётся точкой отсчёта."""
    subject = query.get("review_subject")
    raw_cycle = query.get("review_cycle_id", "")
    if subject not in MOCK_SUBJECTS or not raw_cycle.isascii() or not raw_cycle.isdecimal() or len(raw_cycle) > 10:
        return None
    cycle_id = int(raw_cycle)
    queue = build_review_queue(
        db, curator_id, subject=subject, tariff=query.get("review_tariff", ""),
        cycle_id=cycle_id, status=query.get("review_status", ""),
    )
    cycle = queue["period"]["cycle"]
    if not cycle or cycle.id != cycle_id:
        return None
    cards = queue["anchors"]
    current = cards.get(submission.user_id)
    if not current or submission.id not in current["work_ids"]:
        return None
    ids = list(cards)
    index = ids.index(submission.user_id)

    def next_link(candidate_ids):
        for student_id in candidate_ids:
            card = cards[student_id]
            if card["works"]:
                return card["works"][0]["url"]
        return None

    return {
        "previous_url": next_link(reversed(ids[:index])),
        "next_url": next_link(ids[index + 1:]),
        "queue_url": queue["queue_url"],
    }
