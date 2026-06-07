from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session as DBSession

from app.models.user import User


def get_student_for_staff_access(
    db: DBSession,
    user: dict,
    student_id: int,
    *,
    active_only: bool = False,
    exclude_deleted: bool = False,
    not_found_status_code: int = 404,
    not_found_detail: str,
    forbidden_detail: str,
) -> User:
    query = db.query(User).filter(User.id == student_id)
    if active_only:
        query = query.filter(User.is_active == True)  # noqa: E712
    if exclude_deleted:
        query = query.filter(User.deleted_at.is_(None))

    student = query.first()
    if not student:
        raise HTTPException(status_code=not_found_status_code, detail=not_found_detail)

    if user["role_rank"] == 2 and student.curator_id != user["user_id"]:
        raise HTTPException(status_code=403, detail=forbidden_detail)

    return student
