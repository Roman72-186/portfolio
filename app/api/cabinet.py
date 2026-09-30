from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.dependencies import get_current_user
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

# У модератора своего дашборда нет, дом — первый из его разделов. Суперадмин
# может закрыть ему любой из них (services/section_access.py, 30.09.2026) —
# тогда дом переезжает на следующий открытый, иначе вход упирался бы в 403.
MODERATOR_HOMES = (
    ("students", "/cabinet/students"),
    ("archive", "/cabinet/archive"),
    ("statistics", "/cabinet/superadmin/activity"),
)


@router.get("")
def cabinet_home(user: Annotated[dict, Depends(get_current_user)]):
    if user["role_name"] == "модератор":
        closed = user.get("closed_sections") or frozenset()
        for section_key, href in MODERATOR_HOMES:
            if section_key not in closed:
                return RedirectResponse(href, status_code=302)
        raise HTTPException(status_code=403, detail=SECTION_CLOSED_DETAIL)
    dest = ROLE_CABINET_MAP.get(user["role_name"], "/cabinet/student")
    return RedirectResponse(dest, status_code=302)
