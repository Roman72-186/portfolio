"""Admin/superadmin tool: assign and remove free-text tags on students."""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
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
    parse_usernames,
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
            query = query.filter(User.profile_completed == True)  # noqa: E712

        # @username — это Telegram-логин; tg_username зашифрован (EncryptedString),
        # поэтому ищем по нему в Python, а не через SQL ilike. Имя/фамилию матчим тоже
        # в памяти для единообразия.
        q_clean = q.strip().lstrip("@")
        all_students = query.order_by(User.last_name, User.first_name).all()
        if q_clean:
            ql = q_clean.lower()
            students = [
                s for s in all_students
                if (s.first_name and ql in s.first_name.lower())
                or (s.last_name and ql in s.last_name.lower())
                or (s.name and ql in s.name.lower())
                or (s.tg_username and ql in s.tg_username.lower())
            ]
        else:
            students = all_students

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


@router.post("/tags/bulk-lookup")
def superadmin_bulk_lookup(
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    usernames: str = Form(""),
):
    requested = parse_usernames(usernames)
    if not requested:
        return JSONResponse({"ok": True, "matched": [], "not_found": []})

    requested_set = set(requested)
    student_role = db.query(Role).filter(Role.rank == 1).first()
    matched_users: dict[str, User] = {}
    if student_role:
        query = db.query(User).filter(
            User.role_id == student_role.id,
            User.is_active == True,  # noqa: E712
            User.deleted_at.is_(None),
        )
        if user["role_rank"] < 5:
            query = query.filter(User.profile_completed == True)  # noqa: E712
        candidates = query.all()
        # tg_username зашифрован (EncryptedString) — сравниваем в Python после расшифровки
        for candidate in candidates:
            uname = (candidate.tg_username or "").strip().lstrip("@").lower()
            if uname in requested_set and uname not in matched_users:
                matched_users[uname] = candidate

    tags_by_user = get_tags_for_users(db, [u.id for u in matched_users.values()])

    matched = []
    not_found = []
    for uname in requested:
        target = matched_users.get(uname)
        if not target:
            not_found.append(uname)
            continue
        tag_ids_by_name = {t.name: t.id for t in tags_by_user.get(target.id, [])}
        matched.append({
            "user_id": target.id,
            "username": uname,
            "display_name": f"{target.last_name or ''} {target.first_name or target.name}".strip(),
            "tags": {
                "Р": tag_ids_by_name.get("Р"),
                "К": tag_ids_by_name.get("К"),
                "Р+К": tag_ids_by_name.get("Р+К"),
            },
        })

    return JSONResponse({"ok": True, "matched": matched, "not_found": not_found})


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
