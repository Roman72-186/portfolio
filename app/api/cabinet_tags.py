"""Admin/superadmin tool: assign and remove free-text tags on students."""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf
from app.models.role import Role
from app.models.user import User
from app.services.tags import (
    add_tag_to_user,
    ensure_profile_tags,
    get_curator_names,
    get_or_create_tag,
    get_suggested_tags,
    get_tags_for_users,
    remove_tag_from_user,
)
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/superadmin")


def _parse_bool(s: str) -> bool:
    return s.lower() in ("1", "true", "yes", "on")


@router.get("/tags", response_class=HTMLResponse)
def superadmin_tags_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    q: str = Query(""),
    show_hidden: str = Query(""),
):
    student_role = db.query(Role).filter(Role.rank == 1).first()
    students: list[User] = []
    if student_role:
        query = db.query(User).filter(
            User.role_id == student_role.id,
            User.is_active == True,  # noqa: E712
            User.deleted_at.is_(None),
        )

        show_hidden_b = user["role_rank"] >= 5 and _parse_bool(show_hidden)
        if not show_hidden_b:
            query = query.filter(User.course_periods.isnot(None), User.lessons_count.isnot(None))

        q_clean = q.strip()
        if q_clean:
            like = f"%{q_clean}%"
            query = query.filter(
                or_(User.first_name.ilike(like), User.last_name.ilike(like), User.name.ilike(like))
            )

        students = query.order_by(User.last_name, User.first_name).all()

    ensure_profile_tags(db, students)
    tags_by_user = get_tags_for_users(db, [s.id for s in students])
    suggested_tags = get_suggested_tags(db)
    curator_names = get_curator_names(db)

    return templates.TemplateResponse("superadmin_tags.html", {
        "request": request,
        "user": user,
        "students": students,
        "tags_by_user": tags_by_user,
        "suggested_tags": suggested_tags,
        "curator_names": curator_names,
        "q": q,
        "show_hidden": "1" if (user["role_rank"] >= 5 and _parse_bool(show_hidden)) else "",
        "is_superadmin": user["role_rank"] >= 5,
    })


@router.post("/tags/{user_id}")
def superadmin_add_tag(
    user_id: int,
    _user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    name: str = Form(""),
):
    target = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    name_clean = name.strip()
    if not name_clean:
        raise HTTPException(status_code=400, detail="Тег не может быть пустым")
    if len(name_clean) > 50:
        raise HTTPException(status_code=400, detail="Тег слишком длинный (макс. 50 символов)")

    tag = get_or_create_tag(db, name_clean)
    add_tag_to_user(db, user_id, tag.id)
    db.commit()

    return JSONResponse({"ok": True, "tag": {"id": tag.id, "name": tag.name}})


@router.delete("/tags/{user_id}/{tag_id}")
def superadmin_remove_tag(
    user_id: int,
    tag_id: int,
    _user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    remove_tag_from_user(db, user_id, tag_id)
    db.commit()
    return JSONResponse({"ok": True})
