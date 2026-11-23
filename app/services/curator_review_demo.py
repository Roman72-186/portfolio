"""Доступ руководителя к новой проверке в режиме демо."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.models.user import User


# Учётная запись Лизы с ролью ГП, а не её ученический аккаунт.
LIZA_HEAD_USER_ID = 10


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


def demo_review_url(url: str, curator_id: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(review_demo="1", review_curator=str(curator_id))
    return urlunsplit(parts._replace(query=urlencode(query)))


def can_use_demo_recording(user: dict, selected_curator: str | None) -> bool:
    """A curator queue previews the curator controls, even in a head session."""
    return (
        user.get("role_name") == "админ"
        and (user.get("nav_rank") or user["role_rank"]) == 4
        and selected_curator in (None, str(user["user_id"]))
    )
