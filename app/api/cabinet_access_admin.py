"""Экран «Доступы»: суперадмин задаёт сотрудникам уровень в разделах кабинета —
«Нет · Смотреть · Менять» (владелец 30.09 и 04.10.2026).

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
    levels = section_access.role_levels(db)

    def actions(role_name: str, section_key: str | None) -> list[dict]:
        """Действия под строкой раздела: галочка, подпись, подтверждение."""
        return [
            {
                "action": action,
                "level": levels[role_name][action.key],
                "risky": action.risky
                and section_access.native_action_level(action, role_name) != section_access.LEVEL_EDIT,
            }
            for action in section_access.ACTIONS
            if action.section == section_key
        ]

    # Строки экрана по ролям: уровень сейчас, доступные уровни, какие из них
    # открывают чужие аккаунты (подтверждение перед сохранением), действия.
    rows = {
        col["name"]: [
            {
                "section": section,
                "level": levels[col["name"]][section.key],
                "levels": section_access.levels_of(section),
                "native": section_access.is_native(section, col["name"]),
                "risky": [
                    lv for lv in section_access.levels_of(section)
                    if section_access.is_risky(section, col["name"], lv)
                ],
                "actions": actions(col["name"], section.key),
            }
            for section in section_access.SECTIONS
        ]
        for col in columns
    }
    loose_actions = {col["name"]: actions(col["name"], None) for col in columns}
    return templates.TemplateResponse(request, "superadmin_access.html", {
        "request": request,
        "user": user,
        "columns": columns,
        "rows": rows,
        "loose_actions": loose_actions,
        "level_labels": section_access.LEVEL_TITLES,
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
    desired: dict[str, dict[str, str]] = {}
    for name, slug in _ROLE_SLUGS.items():
        for section in section_access.SECTIONS:
            # Ячейка — группа радиокнопок с уровнем. Не пришла — не трогаем.
            value = form.get(f"cell__{slug}__{section.key}")
            if value is not None:
                desired.setdefault(name, {})[section.key] = str(value)
        for action in section_access.ACTIONS:
            # Действие — скрытое «none» и галочка «edit» с одним именем:
            # отмеченная приходит как ["none", "edit"], снятая — как ["none"].
            values = form.getlist(f"cell__{slug}__{action.key}")
            if values:
                desired.setdefault(name, {})[action.key] = (
                    section_access.LEVEL_EDIT if section_access.LEVEL_EDIT in values
                    else section_access.LEVEL_NONE
                )
    changes = section_access.save_role_levels(db, actor_id=user["user_id"], desired=desired)
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
    keys = [s.key for s in section_access.SECTIONS] + [a.key for a in section_access.ACTIONS]
    desired = {
        key: str(form.get(f"section__{key}"))
        for key in keys
        if form.get(f"section__{key}") is not None
    }
    try:
        section_access.save_user_rules(db, actor_id=user["user_id"], target=target, desired=desired)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(
        f"/cabinet/superadmin/users/{target.id}?saved=access#section-access", status_code=303,
    )
