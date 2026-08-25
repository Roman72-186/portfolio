"""«Личная информация» — вкладка ученика (трек A, A1.4+A1.5).

Два экрана:

- `/cabinet/personal` — просмотр: свои контакты + статичные заглушки документов
  (оферта/ПНД/чеки/FAQ), которых в проекте пока не существует;
- `/cabinet/personal/contacts` — правка **только контактов**: телефон, телефон
  родителя, ник в Telegram.

Установочные данные (ФИО, тариф, месяц/год начала обучения, год поступления в
вуз) ученик заполняет один раз в анкете первого входа `/cabinet/profile` и
дальше не меняет — их правит куратор через
`POST /cabinet/students/{student_id}/profile`. На экране контактов они видны,
но заблокированы: так ученик понимает, что данные учтены и куда идти за
правкой. Owner-решение 25.08.2026.

Только self-view: staff-просмотр чужой личной информации через этот роут не
подключён ни к одному экрану персонала — заводить нечего, пока не появится
реальный сценарий.
"""
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.cache import invalidate_session
from app.constants import TARIFF_DISPLAY, MONTHS
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.user import User
from app.services.contacts import (
    find_student_by_tg_username,
    normalize_phone,
    normalize_tg_username,
    validate_contacts,
)
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
        "saved": request.query_params.get("saved") == "1",
    })


def _contacts_ctx(request, user, errors=None, form=None):
    """Контекст экрана правки. `locked` — установочные данные для показа."""
    enrolled_at = user.get("enrolled_at")
    enrollment_month = MONTHS[enrolled_at.month - 1].capitalize() if enrolled_at else None

    return {
        "request": request,
        "user": user,
        "locked": {
            "name": user.get("name") or "",
            "tariff": TARIFF_DISPLAY.get(user.get("tariff") or "", user.get("tariff") or ""),
            "enrollment_month": enrollment_month,
            "enrollment_year": user.get("enrollment_year"),
            "university_year": user.get("university_year"),
        },
        "form": form or {
            "phone": user.get("phone") or "",
            "parent_phone": user.get("parent_phone") or "",
            "tg_username": user.get("tg_username") or "",
        },
        **({"errors": errors} if errors else {}),
    }


@router.get("/personal/contacts", response_class=HTMLResponse)
def cabinet_personal_contacts(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    return templates.TemplateResponse(
        "cabinet_personal_contacts.html", _contacts_ctx(request, user)
    )


@router.post("/personal/contacts", response_class=HTMLResponse)
def cabinet_personal_contacts_save(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    phone: Annotated[str, Form()] = "",
    parent_phone: Annotated[str, Form()] = "",
    tg_username: Annotated[str, Form()] = "",
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    phone = normalize_phone(phone)
    parent_phone = normalize_phone(parent_phone)
    tg_username = normalize_tg_username(tg_username)

    errors = validate_contacts(phone, parent_phone, tg_username)
    # Ник — ключ поиска: по нему n8n находит папку с работами в Google Drive
    # (services/drive.py) и суперадмин заводит учеников пачкой
    # (cabinet_superadmin.py). Двое с одним ником сливаются в одну карточку.
    if not errors and find_student_by_tg_username(
        db, tg_username, exclude_user_id=user["user_id"]
    ):
        errors.append("Такой ник в Telegram уже занят другим учеником. Проверьте написание.")

    if errors:
        form = {"phone": phone, "parent_phone": parent_phone, "tg_username": tg_username}
        return templates.TemplateResponse(
            "cabinet_personal_contacts.html",
            _contacts_ctx(request, user, errors=errors, form=form),
        )

    # Поля из формы — только контакты. Всё остальное в записи не трогаем даже
    # значением по умолчанию: установочные данные принадлежат куратору.
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    db_user.phone = phone
    db_user.parent_phone = parent_phone
    db_user.tg_username = tg_username
    db.commit()
    # Иначе Redis продолжит отдавать старые контакты на всех экранах ученика.
    invalidate_session(user["session_id"])

    return RedirectResponse("/cabinet/personal?saved=1", status_code=302)
