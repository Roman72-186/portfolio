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
import asyncio
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.cache import invalidate_session
from app.constants import TARIFF_DISPLAY, MONTHS, PAYMENT_URL, SUPPORT_URL
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.user import User
from app.services.skills_history import skills_history
from app.services import telegram as telegram_service
from app.services.tz import msk_text
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
    db: Annotated[DBSession, Depends(get_db)],
):
    # Порядок проверок важен: у ученика с закрытым доступом анкету не просим.
    # `/cabinet/profile` закрыт тем же запретом и отбрасывает обратно сюда —
    # редирект на него первым закольцевал бы страницу насмерть. Да и смысла в
    # анкете уже нет: доступ кончился, человек пришёл решать про оплату.
    if not user.get("access_expired") and needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    return templates.TemplateResponse(request, "cabinet_personal.html", {
        "request": request,
        "user": user,
        "saved": request.query_params.get("saved") == "1",
        "support_url": SUPPORT_URL,
        "payment_url": PAYMENT_URL,
        "access_until_text": msk_text(user.get("access_until")),
        # Динамика самооценки навыков (владелец 03.09.2026): «в начале
        # обучения было так, в середине уже вот так» — сравнение по датам.
        "skills": skills_history(db, user["user_id"]),
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

    return templates.TemplateResponse(request, "cabinet_personal_contacts.html", _contacts_ctx(request, user)
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

    # Живая сверка с Telegram (12.09.2026) — иначе ученик мог бы снять
    # блокировку tg_username_mismatch, просто перепечатав тот же неверный
    # ник. telegram_chat_id есть только у входивших через бота. Одна короткая
    # попытка (не 3×15с из get_chat_username по умолчанию — это интерактивная
    # форма, ученик ждёт с пальцем на кнопке, а не ночной фон), чтобы не
    # держать HTTP-запрос почти минуту при недоступном Telegram.
    tg_mismatch_after_save: bool | None = None
    was_blocked = False
    if not errors:
        db_user = db.query(User).filter(User.id == user["user_id"]).first()
        was_blocked = db_user.tg_username_mismatch
        if db_user.telegram_chat_id:
            ok, live_username = asyncio.run(
                telegram_service.get_chat_username(
                    db_user.telegram_chat_id, max_attempts=1, timeout=6.0,
                )
            )
            if ok:
                live_norm = normalize_tg_username(live_username or "").lower()
                tg_mismatch_after_save = tg_username.lower() != live_norm
                if tg_mismatch_after_save:
                    if live_username:
                        errors.append(
                            f"В Telegram сейчас указан другой ник — @{live_username}. "
                            "Проверьте написание или обновите ник в настройках Telegram."
                        )
                    else:
                        errors.append(
                            "В Telegram у вас сейчас не задан публичный ник. "
                            "Установите его в настройках Telegram (Имя пользователя), "
                            "затем сохраните здесь ещё раз."
                        )
            elif was_blocked:
                # Сохранение именно сейчас важно ученику, чтобы снять блокировку —
                # промолчать и сохранить контакты «как получится» означало бы
                # показать «Контакты сохранены» и тут же баннер «Доступ закрыт»
                # под ним, с текстом, который обещает обратное. Честнее отказать
                # и попросить повторить: прежний флаг при этом не трогаем совсем.
                errors.append(
                    "Не удалось проверить ник в Telegram прямо сейчас — Telegram "
                    "не ответил. Остальные контакты не сохранены, попробуйте ещё "
                    "раз через минуту."
                )

    if errors:
        form = {"phone": phone, "parent_phone": parent_phone, "tg_username": tg_username}
        return templates.TemplateResponse(request, "cabinet_personal_contacts.html",
            _contacts_ctx(request, user, errors=errors, form=form),
        )

    # Поля из формы — только контакты. Всё остальное в записи не трогаем даже
    # значением по умолчанию: установочные данные принадлежат куратору.
    db_user.phone = phone
    db_user.parent_phone = parent_phone
    db_user.tg_username = tg_username
    if tg_mismatch_after_save is not None:
        db_user.tg_username_mismatch = tg_mismatch_after_save
    db.commit()
    # Иначе Redis продолжит отдавать старые контакты на всех экранах ученика.
    invalidate_session(user["session_id"])

    return RedirectResponse("/cabinet/personal?saved=1", status_code=302)
