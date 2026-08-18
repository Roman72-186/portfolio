from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.dependencies import get_current_user

router = APIRouter(prefix="/cabinet")

ROLE_CABINET_MAP = {
    "суперадмин": "/cabinet/superadmin",
    "админ":      "/cabinet/admin-panel",
    "модератор":  "/cabinet/student",
    "куратор":    "/cabinet/curator",
    "ученик":     "/cabinet/learning",
}


@router.get("")
def cabinet_home(user: Annotated[dict, Depends(get_current_user)]):
    dest = ROLE_CABINET_MAP.get(user["role_name"], "/cabinet/student")
    return RedirectResponse(dest, status_code=302)
