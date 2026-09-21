"""«Личная информация» — вкладка ученика (трек A, A1.4+A1.5).

Два экрана:

- `/cabinet/personal` — просмотр: свои контакты + документы. Согласие на
  обработку ПДн, политика обработки ПДн и согласие на рассылку лежат как .docx
  в `app/static/legal/` (добавлены 13.09.2026), оферта — как PDF
  `oferta-2026.pdf` (владелец прислал новую редакцию 16.09.2026 только в PDF,
  .docx удалён, чтобы не отдавать две версии договора с разными ценами).
  Одновременно каждый документ лежит HTML-текстом в
  `app/templates/partials/legal/*.html` — конвертация одноразовая (исходник →
  HTML, скрипт не хранится в репозитории), при правке исходного документа
  переносить правки вручную. Строка открывает поп-ап с текстом
  (`GET /cabinet/personal/legal/{slug}` отдаёт HTML-фрагмент), внизу — ссылка
  «Скачать .pdf»/«Скачать .docx», подпись собирается из расширения файла в
  `partials/legal_doc_modal.html`. Чеки и FAQ — по-прежнему заглушки, их в
  проекте ещё нет;
- `/cabinet/personal/legal/{slug}` — HTML-фрагмент документа для поп-апа,
  без `base.html` (голая разметка, не страница);
- `/cabinet/personal/contacts` — правка личных данных: ФИО, дата рождения,
  контакты, данные родителя, город, часовой пояс, email, ссылка ВКонтакте,
  адрес СДЭК и год поступления в вуз. Тариф и начало обучения остаются
  системными данными: они определяют доступ и учебный прогресс, поэтому
  ученик видит их в форме, но не меняет.

Только self-view: staff-просмотр чужой личной информации через этот роут не
подключён ни к одному экрану персонала — заводить нечего, пока не появится
реальный сценарий.
"""
import asyncio
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import (
    EMAIL_RE,
    VK_RE,
    _MIN_BIRTH_YEAR,
    _university_year_options,
    needs_profile_setup,
    normalize_vk_profile_url,
)
from app.cache import invalidate_session
from app.constants import TARIFF_DISPLAY, MONTHS, PAYMENT_URL, SUPPORT_URL, TIMEZONES
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.user import User
from app.models.tracker import ITEM_ARCHI_PROFILE, TrackerTask
from app.models.task_block import TaskBlockResponse
from app.services.archi_profile import result_for_answers
from app.services.skills_history import skills_history
from app.services import telegram as telegram_service
from app.services.tz import msk_text, today_msk
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

    diagnostic_responses = (
        db.query(TaskBlockResponse)
        .join(TrackerTask, TrackerTask.id == TaskBlockResponse.task_id)
        .filter(
            TaskBlockResponse.user_id == user["user_id"],
            TrackerTask.kind == ITEM_ARCHI_PROFILE,
        )
        .order_by(TaskBlockResponse.updated_at.desc(), TaskBlockResponse.id.desc())
        .all()
    )
    archi_profile = next(
        (
            result
            for response in diagnostic_responses
            if (result := result_for_answers(db, response.task_id, user["user_id"]))
        ),
        None,
    )

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
        "archi_profile": archi_profile,
    })


def _contacts_ctx(request, user, errors=None, form=None):
    """Контекст единой формы самостоятельной правки личных данных."""
    enrolled_at = user.get("enrolled_at")
    enrollment_month = MONTHS[enrolled_at.month - 1].capitalize() if enrolled_at else None

    return {
        "request": request,
        "user": user,
        "locked": {
            "tariff": TARIFF_DISPLAY.get(user.get("tariff") or "", user.get("tariff") or ""),
            "enrollment_month": enrollment_month,
            "enrollment_year": user.get("enrollment_year"),
        },
        "form": form or {
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "birth_date": user.get("birth_date").isoformat() if user.get("birth_date") else "",
            "phone": user.get("phone") or "",
            "parent_phone": user.get("parent_phone") or "",
            "parent_name": user.get("parent_name") or "",
            "tg_username": user.get("tg_username") or "",
            "city": user.get("city") or "",
            "timezone": user.get("timezone") or "",
            "email": user.get("email") or "",
            "vk_profile_url": user.get("vk_profile_url") or "",
            "sdek_address": user.get("sdek_address") or "",
            "university_year": user.get("university_year") or "",
        },
        "birth_date_min": f"{_MIN_BIRTH_YEAR}-01-01",
        "birth_date_max": today_msk().isoformat(),
        "university_years": _university_year_options(user),
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
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    birth_date: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    parent_phone: Annotated[str, Form()] = "",
    parent_name: Annotated[str, Form()] = "",
    tg_username: Annotated[str, Form()] = "",
    city: Annotated[str, Form()] = "",
    contacts_timezone: Annotated[str, Form(alias="timezone")] = "",
    email: Annotated[str, Form()] = "",
    vk_profile_url: Annotated[str, Form()] = "",
    sdek_address: Annotated[str, Form()] = "",
    university_year: Annotated[str, Form()] = "",
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    first_name = first_name.strip()
    last_name = last_name.strip()
    birth_date = birth_date.strip()
    phone = normalize_phone(phone)
    parent_phone = normalize_phone(parent_phone)
    parent_name = parent_name.strip()
    tg_username = normalize_tg_username(tg_username)
    city = city.strip()
    contacts_timezone = contacts_timezone.strip()
    email = email.strip().lower()
    vk_profile_url = normalize_vk_profile_url(vk_profile_url)
    sdek_address = sdek_address.strip()
    university_year = university_year.strip()

    errors = validate_contacts(phone, parent_phone, tg_username)

    if not first_name:
        errors.append("Введи имя")
    elif len(first_name) > 50:
        errors.append("Имя слишком длинное (максимум 50 символов)")

    if not last_name:
        errors.append("Введи фамилию")
    elif len(last_name) > 50:
        errors.append("Фамилия слишком длинная (максимум 50 символов)")

    parsed_birth_date = None
    if birth_date:
        try:
            parsed_birth_date = date.fromisoformat(birth_date)
            if parsed_birth_date > today_msk():
                errors.append("Дата рождения не может быть в будущем")
            elif parsed_birth_date.year < _MIN_BIRTH_YEAR:
                errors.append("Проверь дату рождения")
        except ValueError:
            errors.append("Дата рождения указана неверно")
    else:
        errors.append("Укажи дату рождения")

    if not parent_name:
        errors.append("Введи имя и отчество родителя")
    elif len(parent_name) > 150:
        errors.append("Имя родителя слишком длинное")

    parsed_university_year = None
    if university_year:
        try:
            parsed_university_year = int(university_year)
            if not 2000 <= parsed_university_year <= 2100:
                errors.append("Год поступления в вуз должен быть реальным годом")
        except ValueError:
            errors.append("Год поступления в вуз должен быть числом")
    else:
        errors.append("Укажи год поступления в вуз")

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
            "first_name": first_name, "last_name": last_name,
            "birth_date": birth_date,
            "phone": phone, "parent_phone": parent_phone, "tg_username": tg_username,
            "parent_name": parent_name,
            "city": city, "timezone": contacts_timezone, "email": email,
            "vk_profile_url": vk_profile_url, "sdek_address": sdek_address,
            "university_year": university_year,
        }
        return templates.TemplateResponse(request, "cabinet_personal_contacts.html",
            _contacts_ctx(request, user, errors=errors, form=form),
        )

    db_user.first_name = first_name
    db_user.last_name = last_name
    db_user.name = f"{first_name} {last_name}"
    db_user.birth_date = parsed_birth_date
    db_user.phone = phone
    db_user.parent_phone = parent_phone
    db_user.parent_name = parent_name
    db_user.tg_username = tg_username
    db_user.city = city
    db_user.timezone = contacts_timezone
    db_user.email = email
    db_user.vk_profile_url = vk_profile_url
    db_user.sdek_address = sdek_address
    db_user.university_year = parsed_university_year
    if tg_mismatch_after_save is not None:
        db_user.tg_username_mismatch = tg_mismatch_after_save
    db.commit()
    # Иначе Redis продолжит отдавать старые контакты на всех экранах ученика.
    invalidate_session(user["session_id"])

    return RedirectResponse("/cabinet/personal?saved=1", status_code=302)
