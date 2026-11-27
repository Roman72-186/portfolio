"""Доступ к новой проверке в режиме демо."""
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.models.task_block import TaskBlockSubmission
from app.models.user import User


# Учётная запись Лизы с ролью ГП, а не её ученический аккаунт.
LIZA_HEAD_USER_ID = 10
# Персональная проверка сданной контрольной по рисунку для Алёны и Романа.
ALENA_CURATOR_USER_ID = 169
ROMAN_DEMO_STUDENT_ID = 347
ROMAN_DRAWING_CONTROL_SUBMISSION_ID = 4306


def can_preview_review(db: Session, user: dict) -> bool:
    is_head = (
        (user.get("nav_rank") or user["role_rank"]) == 4
        and user.get("role_name") == "админ"
    )
    original_id = user.get("impersonated_by_id")
    if not original_id:
        return is_head
    original = db.get(User, original_id)
    if not original or not original.is_active or original.deleted_at or not original.role:
        return False
    if original.role.rank == 4 and original.role.name == "админ":
        return True
    return (
        original.role.rank == 5 and original.role.name == "суперадмин"
        and is_head and user.get("user_id") == LIZA_HEAD_USER_ID
    )


def has_personal_demo_review(db: Session, user: dict, submission_id: int) -> bool:
    if (
        submission_id != ROMAN_DRAWING_CONTROL_SUBMISSION_ID
        or user.get("user_id") != ALENA_CURATOR_USER_ID
        or user.get("role_name") != "куратор"
    ):
        return False
    original_id = user.get("impersonated_by_id")
    if original_id:
        original = db.get(User, original_id)
        if not (
            original and original.is_active and not original.deleted_at
            and original.role and original.role.rank == 5 and original.role.name == "суперадмин"
        ):
            return False
    submission = db.get(TaskBlockSubmission, submission_id)
    if submission is None or submission.user_id != ROMAN_DEMO_STUDENT_ID:
        return False
    student = db.get(User, submission.user_id)
    return bool(
        student and student.is_active and not student.deleted_at and not student.archived_at
        and student.curator_id == user["user_id"]
    )


def can_preview_submission_review(db: Session, user: dict, submission_id: int) -> bool:
    return can_preview_review(db, user) or has_personal_demo_review(db, user, submission_id)


def can_handle_late_controls(user: dict) -> bool:
    return user["user_id"] == LIZA_HEAD_USER_ID and not user.get("impersonated_by_id")


def demo_review_url(url: str, curator_id: int | str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(review_demo="1", review_curator=str(curator_id))
    return urlunsplit(parts._replace(query=urlencode(query)))


def demo_review_query(query: Mapping[str, str]) -> dict[str, str]:
    keys = ("review_subject", "review_tariff", "review_cycle_id", "review_status", "review_curator")
    return {"review_demo": "1", **{key: query[key] for key in keys if key in query}}


def can_use_demo_recording(user: dict, selected_curator: str | None) -> bool:
    """A curator queue previews the curator controls, even in a head session."""
    return (
        user.get("role_name") == "админ"
        and (user.get("nav_rank") or user["role_rank"]) == 4
        and selected_curator in (None, "all", str(user["user_id"]))
    )
