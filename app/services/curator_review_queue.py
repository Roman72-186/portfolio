"""Карточки и переходы проверки используют одну очередь куратора."""
from datetime import datetime
from urllib.parse import urlencode

from sqlalchemy.orm import Session, selectinload

from app.constants import MOCK_SUBJECTS
from app.models.task_block import TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback
from app.models.user import User
from app.services.curator_demo import _assignments, _period
from app.services.staff_dashboard import CURATOR_STATS_TARIFFS
from app.services.task_block_feedback import current_submission_has_feedback
from app.services.task_blocks import submission_review_queue
from app.services.tz import MSK_TZ

QUEUE_URL = "/cabinet/staff/students-review/queue"
CONTROL_FILTERS = {
    "missing": "Не сдали",
    "full": "Сдали вовремя",
    "text": "Сдали после первого дедлайна",
    "score": "Сдали после второго дедлайна",
}


def _feedback_url(submission_id: int, query: str) -> str:
    return f"/cabinet/staff/task-block-submissions/{submission_id}/feedback?{query}"


def build_review_queue(
    db: Session, curator_id: int | None, *, subject: str, tariff: str, cycle_id: int | None, status: str,
    include_late: bool = False,
) -> dict:
    subject = subject if subject in MOCK_SUBJECTS else "Рисунок"
    tariff = tariff if tariff in CURATOR_STATS_TARIFFS else ""
    status = status if status == "all" or status in CONTROL_FILTERS else ""
    period = _period(db, datetime.now(MSK_TZ).date(), None, cycle_id)
    cycle = period["cycle"]
    filters = {"subject": subject, "tariff": tariff, "status": status}
    if cycle:
        filters["cycle_id"] = cycle.id
    feedback_query = urlencode({f"review_{key}": value for key, value in filters.items()})
    cards = {}
    if cycle:
        rows = submission_review_queue(
            db, curator_id=curator_id, subject=subject, tariff=tariff or None,
            cycle_id=cycle.id, limit=1000,
        )
        students_query = db.query(User).filter(User.is_active.is_(True), User.deleted_at.is_(None))
        if curator_id is not None:
            students_query = students_query.filter(User.curator_id == curator_id)
        if tariff:
            students_query = students_query.filter(User.tariff == tariff)
        if status != "missing":
            students_query = students_query.filter(User.id.in_({row["student_id"] for row in rows}))
        students = {student.id: student for student in students_query}
        if status == "missing":
            assignments = _assignments(db, cycle, list(students.values()), datetime.now(MSK_TZ))
            control_ids = {
                item["block_id"] for assigned in assignments.values() for item in assigned
                if item["control"] and item["subject"] == subject
            }
            submitted = set(db.query(TaskBlockSubmission.user_id, TaskBlockSubmission.block_id).filter(
                TaskBlockSubmission.user_id.in_(assignments),
                TaskBlockSubmission.block_id.in_(control_ids),
                TaskBlockSubmission.submitted_at.is_not(None),
            )) if control_ids else set()
            for student_id, assigned in assignments.items():
                works = [
                    {"url": None, "images": [], "block_title": item["label"]}
                    for item in assigned if item["control"] and item["subject"] == subject
                    and (student_id, item["block_id"]) not in submitted
                ]
                if works:
                    cards[student_id] = {"student": students[student_id], "works": works, "work_ids": set()}
        groups_missing_scores = {
            row["submission_id"]: row["pending_controls"]
            for row in rows if row["control_review_mode"] != "score"
            and any(work.score is None for work in row["pending_controls"])
        }
        waiting_for_scores = {
            feedback.submission_id for feedback in (
                db.query(TaskBlockFeedback)
                .options(selectinload(TaskBlockFeedback.messages))
                .filter(TaskBlockFeedback.submission_id.in_(groups_missing_scores))
            )
            if all(current_submission_has_feedback(work, feedback)
                   for work in groups_missing_scores[feedback.submission_id])
        } if groups_missing_scores else set()
        # Одинаковое время сдачи не должно переставлять учеников при переходах.
        for row in sorted(rows, key=lambda item: (item["submitted_at"], item["submission_id"]), reverse=True):
            student_id = row["student_id"]
            mode = row["control_review_mode"]
            if student_id not in students or status == "missing" or (mode == "score" and not include_late):
                continue
            card = cards.setdefault(student_id, {"student": students[student_id], "works": [], "work_ids": set()})
            card["work_ids"].add(row["submission_id"])
            if status in CONTROL_FILTERS and row["control_filter_mode"] != status:
                continue
            if not status and (not row["requires_review"] or row["reviewed"] or row["needs_revision"]):
                continue
            waits_for_score = not row["reviewed"] and not row["needs_revision"] and (
                mode == "score" or row["submission_id"] in waiting_for_scores
            )
            card["works"].append({
                **row, "waits_for_score": waits_for_score,
                "url": _feedback_url(row["submission_id"], feedback_query),
            })
    return {
        "subject": subject, "tariff": tariff, "tariffs": CURATOR_STATS_TARIFFS,
        "subject_links": {
            name: QUEUE_URL + "?" + urlencode({**filters, "subject": name})
            for name in MOCK_SUBJECTS
        },
        "period": period, "status": status, "control_filters": CONTROL_FILTERS, "cards": [card for card in cards.values() if card["works"]],
        "anchors": cards, "queue_url": QUEUE_URL + "?" + urlencode(filters),
    }


def review_neighbors(db: Session, curator_id: int | None, submission, query, *, include_late: bool = False) -> dict | None:
    """Ведём по выбранной очереди. Проверенная работа остаётся точкой отсчёта."""
    subject = query.get("review_subject")
    raw_cycle = query.get("review_cycle_id", "")
    if subject not in MOCK_SUBJECTS or not raw_cycle.isascii() or not raw_cycle.isdecimal() or len(raw_cycle) > 10:
        return None
    cycle_id = int(raw_cycle)
    queue = build_review_queue(
        db, curator_id, subject=subject, tariff=query.get("review_tariff", ""),
        cycle_id=cycle_id, status=query.get("review_status", ""), include_late=include_late,
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
