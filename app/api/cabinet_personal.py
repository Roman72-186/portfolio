"""«Личная информация» — вкладка ученика (трек A, A1.4+A1.5).

Скелет: свои контактные данные (только просмотр — редактирование по-прежнему
через /cabinet/profile, форму не дублируем) + статичные заглушки документов
(оферта/ПНД/чеки/FAQ), которых в проекте пока не существует. Только self-view:
staff-просмотр чужой личной информации через этот роут не подключён ни к
одному экрану персонала — заводить нечего, пока не появится реальный сценарий.
"""
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.cabinet_student import needs_profile_setup
from app.dependencies import require_student
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


@router.get("/personal", response_class=HTMLResponse)
def cabinet_personal(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    return templates.TemplateResponse("cabinet_personal.html", {
        "request": request,
        "user": user,
    })
