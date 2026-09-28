from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.dependencies import get_current_user

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


@router.get("")
def cabinet_home(user: Annotated[dict, Depends(get_current_user)]):
    dest = ROLE_CABINET_MAP.get(user["role_name"], "/cabinet/student")
    return RedirectResponse(dest, status_code=302)
