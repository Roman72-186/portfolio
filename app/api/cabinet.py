from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.dependencies import get_current_user
from app.services.navigation import staff_nav_items
from app.services.section_access import SECTION_CLOSED_DETAIL

router = APIRouter(prefix="/cabinet")

ROLE_CABINET_MAP = {
    "суперадмин": "/cabinet/superadmin",
    "админ":      "/cabinet/admin-panel",
    # Модератор — наблюдатель (28.09.2026): дашборд ГП ему закрыт,
    # домашняя страница — «Ученики» (rbac.py::is_moderator_request_allowed).
    "модератор":  "/cabinet/students",
    "куратор":    "/cabinet/curator",
    "ученик":     "/cabinet/learning",
}

# У модератора своего дашборда нет, дом — первый пункт его меню: «Ученики»,
# архив, статистика, затем разделы, открытые ему сверх роли. Суперадмин может
# закрыть любой из них (services/section_access.py) — тогда дом переезжает на
# следующий открытый, иначе вход упирался бы в 403.
@router.get("")
def cabinet_home(user: Annotated[dict, Depends(get_current_user)]):
    if user["role_name"] == "модератор":
        items = staff_nav_items(
            user["nav_rank"], user["role_name"],
            user.get("closed_sections"), user.get("granted_sections"),
        )
        if items:
            return RedirectResponse(items[0].href, status_code=302)
        raise HTTPException(status_code=403, detail=SECTION_CLOSED_DETAIL)
    dest = ROLE_CABINET_MAP.get(user["role_name"], "/cabinet/student")
    return RedirectResponse(dest, status_code=302)
