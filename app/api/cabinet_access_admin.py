"""Экран «Доступы»: суперадмин закрывает сотрудникам разделы кабинета
(владелец 30.09.2026).

Вся логика — в `app/services/section_access.py`, здесь только разбор формы.
Отдельный модуль, а не `cabinet_superadmin.py`: тот и так на три тысячи строк.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_csrf, require_superadmin
from app.models.role import Role
from app.models.user import User
from app.services import section_access
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/superadmin")

# Латинские ключи ролей для имён полей формы.
_ROLE_SLUGS = {
    section_access.ROLE_CURATOR: "curator",
    section_access.ROLE_MODERATOR: "moderator",
    section_access.ROLE_HEAD: "head",
}


@router.get("/access", response_class=HTMLResponse)
def access_page(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
):
    roles = {
        r.name: r
        for r in db.query(Role).filter(Role.name.in_(section_access.CONFIGURABLE_ROLES)).all()
    }
    columns = [
        {
            "name": name,
            "slug": _ROLE_SLUGS[name],
            "label": roles[name].display_name if name in roles else name.capitalize(),
        }
        for name in section_access.CONFIGURABLE_ROLES
    ]
    return templates.TemplateResponse(request, "superadmin_access.html", {
        "request": request,
        "user": user,
        "sections": section_access.SECTIONS,
        "columns": columns,
        "matrix": section_access.role_matrix(db),
        "personal": section_access.staff_with_personal_rules(db),
        "saved": request.query_params.get("saved"),
    })


@router.post("/access")
async def access_save(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    form = await request.form()
    desired: dict[str, dict[str, bool]] = {}
    for name, slug in _ROLE_SLUGS.items():
        for section in section_access.SECTIONS:
            # Скрытое «0» и галочка «1» с одним именем: отмеченная ячейка
            # приходит как ["0", "1"], снятая — как ["0"], отсутствующая —
            # никак, и тогда её не трогаем.
            values = form.getlist(f"cell__{slug}__{section.key}")
            if values:
                desired.setdefault(name, {})[section.key] = "1" in values
    changes = section_access.save_role_matrix(db, actor_id=user["user_id"], desired=desired)
    return RedirectResponse(f"/cabinet/superadmin/access?saved={changes}", status_code=303)


@router.post("/users/{target_id}/access")
async def user_access_save(
    target_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    target = db.query(User).filter(User.id == target_id, User.deleted_at.is_(None)).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    form = await request.form()
    desired = {
        section.key: str(form.get(f"section__{section.key}"))
        for section in section_access.SECTIONS
        if form.get(f"section__{section.key}") is not None
    }
    try:
        section_access.save_user_rules(db, actor_id=user["user_id"], target=target, desired=desired)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(
        f"/cabinet/superadmin/users/{target.id}?saved=access#section-access", status_code=303,
    )
