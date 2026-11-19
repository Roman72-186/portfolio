"""Доступ руководителя к новой проверке в режиме демо."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.models.user import User


def can_preview_review(db: Session, user: dict) -> bool:
    original_id = user.get("impersonated_by_id")
    if original_id:
        original = db.get(User, original_id)
        return bool(
            original and original.is_active and original.role
            and original.role.rank == 4 and original.role.name == "админ"
        )
    return (
        (user.get("nav_rank") or user["role_rank"]) == 4
        and user.get("role_name") == "админ"
    )


def demo_review_url(url: str, curator_id: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(review_demo="1", review_curator=str(curator_id))
    return urlunsplit(parts._replace(query=urlencode(query)))
