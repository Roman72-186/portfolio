"""Вход куратора в свой аккаунт просмотра тарифа и возврат обратно.

Устройство и правила безопасности — в докстринге `app/services/tariff_view.py`.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db.database import get_db
from app.dependencies import get_current_user, require_csrf, require_curator
from app.limiter import limiter
from app.models.audit_log import AuditLog
from app.models.session import Session
from app.services import tariff_view
from app.tmpl import tariff_label, templates

router = APIRouter(prefix="/cabinet/tariff-view")


def _viewer_for(db: DBSession, user: dict):
    # Из режима «глазами» во второй аккаунт не ходят: выход оттуда вернул бы
    # не суперадмина, а куратора.
    if user.get("impersonated_by_id"):
        raise HTTPException(status_code=403, detail="Недоступно в режиме просмотра «глазами»")
    viewer = tariff_view.linked_viewer(db, user["user_id"])
    if viewer is None:
        raise HTTPException(status_code=404, detail="Страница не найдена")
    return viewer


def _render(request: Request, viewer, error: str | None = None):
    label = tariff_label(viewer.viewer_tariff)
    return templates.TemplateResponse(request, "staff_login.html", {
        "request": request,
        "error": error,
        "password_only": True,
        "form_action": "/cabinet/tariff-view",
        "page_title": f"Вход: {label}",
        "page_sub": f"Все ученики тарифа «{label}», только просмотр. Введите пароль этого входа.",
        "back_href": "/cabinet",
        "back_label": "← В кабинет куратора",
    })


@router.get("", response_class=HTMLResponse)
def tariff_view_form(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    return _render(request, _viewer_for(db, user))


@router.post("", response_class=HTMLResponse)
@limiter.limit("10/minute")
def tariff_view_enter(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    password: str = Form(""),
):
    viewer = _viewer_for(db, user)
    if not tariff_view.password_matches(viewer, password):
        return _render(request, viewer, "Неверный пароль")

    session, token = tariff_view.enter(db, viewer, user["session_id"])
    db.add(AuditLog(
        action="tariff_view_enter",
        performed_by_id=user["user_id"],
        target_user_id=viewer.id,
        details=f"session={session.id}",
    ))
    db.commit()

    response = RedirectResponse("/cabinet", status_code=303)
    for key, value in (("session_id", session.id), (tariff_view.RETURN_COOKIE, token)):
        response.set_cookie(
            key=key, value=value, httponly=True, secure=True, samesite="lax",
            max_age=settings.session_ttl_hours * 3600, path="/",
        )
    return response


@router.post("/exit")
def tariff_view_exit(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    # Только аккаунт просмотра: любой другой этим адресом погасил бы себе вход.
    if not user.get("viewer_tariff"):
        raise HTTPException(status_code=404, detail="Страница не найдена")
    curator_session = tariff_view.return_session(
        db, request.cookies.get(tariff_view.RETURN_COOKIE), user["user_id"],
    )
    current = db.get(Session, user["session_id"])
    if current is not None:
        current.is_active = False
    db.commit()

    if curator_session is None:
        # Куратор успел выйти или кука не наша — обратно некуда, только вход.
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("session_id", path="/")
    else:
        response = RedirectResponse("/cabinet", status_code=303)
        response.set_cookie(
            key="session_id", value=curator_session.id, httponly=True, secure=True,
            samesite="lax", max_age=settings.session_ttl_hours * 3600, path="/",
        )
    response.delete_cookie(tariff_view.RETURN_COOKIE, path="/")
    return response
