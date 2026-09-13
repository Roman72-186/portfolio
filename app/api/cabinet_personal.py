"""«Личная информация» — вкладка ученика (трек A, A1.4+A1.5).

Два экрана:

- `/cabinet/personal` — просмотр: свои контакты + документы. Оферта, согласие
  на обработку ПДн, политика обработки ПДн и согласие на рассылку лежат как
  .docx в `app/static/legal/` (добавлены 13.09.2026) и одновременно как
  HTML-текст в `app/templates/partials/legal/*.html` — конвертация одноразовая
  (`.docx` → HTML через python-docx, скрипт не хранится в репозитории), при
  правке исходного документа переконвертировать вручную. Строка открывает
  поп-ап с текстом (`GET /cabinet/personal/legal/{slug}` отдаёт HTML-фрагмент),
  внизу — ссылка «Скачать .docx». Чеки и FAQ — по-прежнему заглушки, их в
  проекте ещё нет;
- `/cabinet/personal/legal/{slug}` — HTML-фрагмент документа для поп-апа,
  без `base.html` (голая разметка, не страница);
- `/cabinet/personal/contacts` — правка контактов: телефон, телефон родителя,
  ник в Telegram, город, часовой пояс, email, ссылка ВКонтакте, адрес СДЭК
  (последние пять открыты владельцем 13.09.2026 — до этого были частью
  установочных данных анкеты и правились только через куратора).

Установочные данные, которые ученик по-прежнему не может изменить сам (ФИО,
дата рождения, имя и отчество родителя, тариф, месяц/год начала обучения, год
поступления в вуз), заполняются один раз в анкете первого входа
`/cabinet/profile` — их правит куратор через
`POST /cabinet/students/{student_id}/profile`. На экране контактов они видны,
но заблокированы: так ученик понимает, что данные учтены и куда идти за
правкой. Owner-решение 25.08.2026.

Только self-view: staff-просмотр чужой личной информации через этот роут не
подключён ни к одному экрану персонала — заводить нечего, пока не появится
реальный сценарий.
"""
import asyncio
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import (
    EMAIL_RE,
    VK_RE,
    needs_profile_setup,
    normalize_vk_profile_url,
)
from app.cache import invalidate_session
from app.constants import TARIFF_DISPLAY, MONTHS, PAYMENT_URL, SUPPORT_URL, TIMEZONES
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


# slug -> HTML-фрагмент документа для поп-апа. Заголовок и ссылка на .docx
# заданы прямо в cabinet_personal.html (там же кнопка вызова) — здесь только
# то, что нужно роуту фрагмента.
LEGAL_DOC_PARTIALS = {
    "oferta": "partials/legal/oferta.html",
    "soglasie-pdn": "partials/legal/soglasie-pdn.html",
    "politika-pdn": "partials/legal/politika-pdn.html",
    "soglasie-rassylka": "partials/legal/soglasie-rassylka.html",
}


@router.get("/personal/legal/{slug}", response_class=HTMLResponse)
def cabinet_personal_legal_doc(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    slug: str,
):
    partial = LEGAL_DOC_PARTIALS.get(slug)
    if partial is None:
        raise HTTPException(404)
    return templates.TemplateResponse(request, partial, {"request": request})


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
            "city": user.get("city") or "",
            "timezone": user.get("timezone") or "",
            "email": user.get("email") or "",
            "vk_profile_url": user.get("vk_profile_url") or "",
            "sdek_address": user.get("sdek_address") or "",
        },
        "timezones": TIMEZONES,
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
    city: Annotated[str, Form()] = "",
    contacts_timezone: Annotated[str, Form(alias="timezone")] = "",
    email: Annotated[str, Form()] = "",
    vk_profile_url: Annotated[str, Form()] = "",
    sdek_address: Annotated[str, Form()] = "",
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    phone = normalize_phone(phone)
    parent_phone = normalize_phone(parent_phone)
    tg_username = normalize_tg_username(tg_username)
    city = city.strip()
    contacts_timezone = contacts_timezone.strip()
    email = email.strip().lower()
    vk_profile_url = normalize_vk_profile_url(vk_profile_url)
    sdek_address = sdek_address.strip()

    errors = validate_contacts(phone, parent_phone, tg_username)

    # Открыты владельцем 13.09.2026 — те же правила, что в анкете первого
    # входа (`profile_post`, app/api/cabinet_student.py), валидация не
    # продублирована случайно, а сознательно держит один канон формата.
    if not city:
        errors.append("Укажи город")
    elif len(city) > 100:
        errors.append("Название города слишком длинное")

    if contacts_timezone not in {tz for tz, _ in TIMEZONES}:
        errors.append("Укажи часовой пояс")

    if not email:
        errors.append("Укажи электронную почту")
    elif not EMAIL_RE.match(email):
        errors.append("Введи корректную электронную почту")

    if not vk_profile_url:
        errors.append("Укажи ссылку на ВКонтакте")
    elif not VK_RE.match(vk_profile_url):
        errors.append("Ссылка на ВКонтакте должна выглядеть как vk.com/имя или vk.ru/имя")

    if not sdek_address:
        errors.append("Укажи ближайший адрес СДЭК")
    elif len(sdek_address) > 300:
        errors.append("Адрес СДЭК слишком длинный")
    # Ник — ключ поиска: по нему n8n находит папку с работами в Google Drive
    # (services/drive.py) и суперадмин заводит учеников пачкой
    # (cabinet_superadmin.py). Двое с одним ником сливаются в одну карточку.
    if not errors and find_student_by_tg_username(
        db, tg_username, exclude_user_id=user["user_id"]
    ):
        errors.append("Такой ник в Telegram уже занят другим учеником. Проверь написание.")

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
                            f"В Telegram сейчас указан другой ник – @{live_username}. "
                            "Проверь написание или обнови ник в настройках Telegram."
                        )
                    else:
                        errors.append(
                            "В Telegram у тебя сейчас не задан публичный ник. "
                            "Установи его в настройках Telegram (Имя пользователя), "
                            "затем сохрани здесь ещё раз."
                        )
            elif was_blocked:
                # Сохранение именно сейчас важно ученику, чтобы снять блокировку —
                # промолчать и сохранить контакты «как получится» означало бы
                # показать «Контакты сохранены» и тут же баннер «Доступ закрыт»
                # под ним, с текстом, который обещает обратное. Честнее отказать
                # и попросить повторить: прежний флаг при этом не трогаем совсем.
                errors.append(
                    "Не удалось проверить ник в Telegram прямо сейчас – Telegram "
                    "не ответил. Остальные контакты не сохранены, попробуй ещё "
                    "раз через минуту."
                )

    if errors:
        form = {
            "phone": phone, "parent_phone": parent_phone, "tg_username": tg_username,
            "city": city, "timezone": contacts_timezone, "email": email,
            "vk_profile_url": vk_profile_url, "sdek_address": sdek_address,
        }
        return templates.TemplateResponse(request, "cabinet_personal_contacts.html",
            _contacts_ctx(request, user, errors=errors, form=form),
        )

    # Поля из формы — контакты и часть анкеты, открытая на самостоятельную
    # правку 13.09.2026 (город/часовой пояс/email/ВК/СДЭК). Остальные
    # установочные поля (ФИО, дата рождения, родитель, тариф, даты обучения)
    # в записи не трогаем — они по-прежнему принадлежат куратору.
    db_user.phone = phone
    db_user.parent_phone = parent_phone
    db_user.tg_username = tg_username
    db_user.city = city
    db_user.timezone = contacts_timezone
    db_user.email = email
    db_user.vk_profile_url = vk_profile_url
    db_user.sdek_address = sdek_address
    if tg_mismatch_after_save is not None:
        db_user.tg_username_mismatch = tg_mismatch_after_save
    db.commit()
    # Иначе Redis продолжит отдавать старые контакты на всех экранах ученика.
    invalidate_session(user["session_id"])

    return RedirectResponse("/cabinet/personal?saved=1", status_code=302)
