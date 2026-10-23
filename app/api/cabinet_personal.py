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
  контакты, данные родителя, город, часовой пояс, email,
  адрес СДЭК и год поступления в вуз. Тариф и начало обучения остаются
  системными данными: они определяют доступ и учебный прогресс, поэтому
  ученик видит их в форме, но не меняет.

Только self-view: staff-просмотр чужой личной информации через этот роут не
подключён ни к одному экрану персонала — заводить нечего, пока не появится
реальный сценарий.
"""
import asyncio
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import (
    EMAIL_RE,
    _MIN_BIRTH_YEAR,
    _university_year_options,
    holiday_tg_username,
    needs_profile_setup,
)
from app.cache import invalidate_session
from app.config import settings
from app.constants import (
    HOLIDAY_TIMEZONE, MONTHS, PAYMENT_URL, PROGRAM_HOLIDAY, SCHOOL_GRADE_LABELS, SCHOOL_GRADES,
    SUPPORT_URL,
    TARIFF_DISPLAY, TIMEZONES,
)
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.intake_link import IntakeLink
from app.models.user import User
from app.models.tracker import ITEM_ARCHI_PROFILE, TrackerTask
from app.services import intake_link as intake_link_service
from app.models.task_block import TaskBlock, TaskBlockResponse
from sqlalchemy import or_
from app.services.archi_profile import result_for_answers
from app.services import payments as payment_service
from app.services.skills_history import skills_history
from app.services import telegram as telegram_service
from app.services.tz import msk_text, today_msk
from app.services.contacts import (
    find_student_by_tg_username,
    normalize_phone,
    normalize_tg_username,
    parse_school_grade,
    validate_contacts,
    validate_holiday_profile,
)
from app.services.tags import ensure_grade_tag
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

    # Диагностика — отдельным видом задания (легаси-путь) или блоком внутри
    # обычного «Задания» (владелец 24.09.2026, второй способ) — оба варианта
    # должны попасть сюда, иначе результаты диагностик, добавленных новым
    # способом, никогда не показались бы ученику.
    diagnostic_task_ids = db.query(TaskBlock.task_id).filter(
        TaskBlock.is_diagnostic.is_(True)
    ).scalar_subquery()
    diagnostic_responses = (
        db.query(TaskBlockResponse)
        .join(TrackerTask, TrackerTask.id == TaskBlockResponse.task_id)
        .filter(
            TaskBlockResponse.user_id == user["user_id"],
            or_(
                TrackerTask.kind == ITEM_ARCHI_PROFILE,
                TrackerTask.id.in_(diagnostic_task_ids),
            ),
        )
        .order_by(TaskBlockResponse.updated_at.desc(), TaskBlockResponse.id.desc())
        .all()
    )
    diagnostic_results = []
    for response in diagnostic_responses:
        result = result_for_answers(db, response.task_id, user["user_id"])
        if result:
            result["diagnostic_title"] = db.get(TrackerTask, response.task_id).title
            diagnostic_results.append(result)

    return templates.TemplateResponse(request, "cabinet_personal.html", {
        "request": request,
        "user": user,
        "saved": request.query_params.get("saved") == "1",
        "support_url": SUPPORT_URL,
        "payment_url": PAYMENT_URL,
        # Ежемесячная оплата (`services/payments.py`): блок с кнопкой
        # «Оплатить» с первого дня окна; `None` — блока нет.
        "payment": payment_service.student_payment_view(
            db, db.get(User, user["user_id"]), datetime.now(timezone.utc),
        ),
        "paid_return": request.query_params.get("paid") == "1",
        # Возврат из Продамуса — та же благодарность, что на его странице и в
        # уведомлении «Оплата принята» (владелец 12.10.2026), но без строки про
        # администратора канала (владелец 13.10.2026).
        "paid_thanks": payment_service.paid_thanks_text(user.get("tariff"), with_contact=False),
        "access_until_text": msk_text(user.get("access_until")),
        # Динамика самооценки навыков (владелец 03.09.2026): «в начале
        # обучения было так, в середине уже вот так» — сравнение по датам.
        "skills": skills_history(db, user["user_id"]),
        "diagnostic_results": diagnostic_results,
        # Каникулярному — только поля его короткой анкеты, без пустых строк
        # годовой (город, родитель, СДЭК), и подпись «Где учишься?».
        "holiday_form": _is_holiday(user),
        "school_grade_label": SCHOOL_GRADE_LABELS.get(user.get("school_grade") or ""),
    })


@router.post("/personal/pay")
def cabinet_personal_pay(
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _: Annotated[None, Depends(require_csrf)],
):
    """Кнопка «Оплатить»: ссылка Продамуса на должный месяц.

    Открыт и при закрытом доступе — адрес под `/cabinet/personal`, иначе
    должнику нечем было бы заплатить. Ответ — JSON с адресом, страница
    уводит на него сама (ключ CSRF берётся свежим, `csrfFetch`).
    """
    if user.get("impersonated_by_id"):
        # Сотрудник «глазами ученика» не должен создавать ученику заказы.
        return JSONResponse({"error": "В режиме просмотра оплата недоступна"}, status_code=403)
    student = db.get(User, user["user_id"])
    try:
        payment = payment_service.get_or_create_payment(
            db, student, datetime.now(timezone.utc), f"https://{settings.domain}",
        )
    except payment_service.PaymentUnavailable as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=409)
    db.commit()
    return JSONResponse({"url": payment.link_url})


@router.get("/personal/track", response_class=HTMLResponse)
def cabinet_personal_track(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Треки каникулярного набора: что можно оплатить и что уже оплачено
    (17.10.2026, план `plans/2026-10-17-apparchi-оплата-трека.md`). Сюда же
    ворота `dependencies.get_current_user` уводят каникулярного без трека, и
    сюда Продамус возвращает после оплаты (`?paid=1`)."""
    if not _is_holiday(user):
        return RedirectResponse("/cabinet/personal", status_code=302)
    owned = intake_link_service.tracks_of(db, user["user_id"])
    owned_ids = {link.id for link in owned}
    return templates.TemplateResponse(request, "cabinet_personal_track.html", {
        "request": request,
        "user": user,
        # Купленные треки и что ещё можно купить: другой трек после
        # купленного — можно (школа 18.10.2026), тот же второй раз — нет.
        "owned": [
            {"title": link.title, "color": intake_link_service.track_color(link.title)}
            for link in owned
        ],
        "tracks": [
            {
                "id": link.id,
                "title": link.title,
                "color": intake_link_service.track_color(link.title),
                "description": link.description or "",
                "price_text": payment_service.rub_text(link.price_kop),
                "access_until_text": msk_text(link.access_until),
            }
            for link in intake_link_service.tracks_on_sale(db)
            if link.id not in owned_ids
        ],
        "paid_return": request.query_params.get("paid") == "1",
        "test_return": request.query_params.get("test") == "1",
        # Служебный аккаунт открывает трек без оплаты — кнопка так и подписана.
        "test_account": payment_service.is_test_account(user["user_id"]),
        # Когда откроется программа (служба заботы 19.10.2026) — пока не
        # открылась; служебные не ждут, им дата не печатается.
        "program_opens_text": (
            msk_text(intake_link_service.program_opens_at())
            if intake_link_service.waits_for_program(user["user_id"], datetime.now(timezone.utc))
            else ""
        ),
        "support_url": SUPPORT_URL,
    })


@router.post("/personal/track/{track_id}/pay")
def cabinet_personal_track_pay(
    track_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _: Annotated[None, Depends(require_csrf)],
):
    """«Оплатить» трек: ссылка Продамуса, ответ — JSON с адресом, как у оплаты
    месяца (`cabinet_personal_pay`). Адрес под `/cabinet/personal` — открыт
    каникулярному до оплаты."""
    if user.get("impersonated_by_id"):
        return JSONResponse({"error": "В режиме просмотра оплата недоступна"}, status_code=403)
    track = db.get(IntakeLink, track_id)
    if track is None or not intake_link_service.is_track(track):
        return JSONResponse({"error": "Такого трека нет"}, status_code=404)
    student = db.get(User, user["user_id"])
    if payment_service.is_test_account(student.id):
        # Служебный аккаунт — тестовый проход без Продамуса (19.10.2026).
        try:
            payment_service.grant_track_for_test(db, student, track, now=datetime.now(timezone.utc))
        except payment_service.PaymentUnavailable as exc:
            db.rollback()
            return JSONResponse({"error": str(exc)}, status_code=409)
        db.commit()
        invalidate_session(user["session_id"])
        return JSONResponse({"url": "/cabinet/personal/track?test=1"})
    try:
        payment = payment_service.get_or_create_track_payment(
            db, student, track, datetime.now(timezone.utc), f"https://{settings.domain}",
        )
    except payment_service.PaymentUnavailable as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=409)
    db.commit()
    return JSONResponse({"url": payment.link_url})


def _is_holiday(user: dict) -> bool:
    return user.get("program") == PROGRAM_HOLIDAY


def _contacts_ctx(request, user, errors=None, form=None):
    """Контекст единой формы самостоятельной правки личных данных.

    Каникулярному набору (`holiday_form`) — те же пять полей, что в его
    короткой анкете: имя, фамилия, ник Telegram, телефон, «Где учишься?»
    (`contacts.validate_holiday_profile`)."""
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
            "sdek_address": user.get("sdek_address") or "",
            "university_year": user.get("university_year") or "",
            "school_grade": user.get("school_grade"),
        },
        "holiday_form": _is_holiday(user),
        "school_grades": SCHOOL_GRADES,
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
    sdek_address: Annotated[str, Form()] = "",
    university_year: Annotated[str, Form()] = "",
    school_grade: Annotated[str, Form()] = "",
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    if _is_holiday(user):
        return _save_holiday_contacts(
            request, user, db,
            first_name=first_name, last_name=last_name, phone=phone,
            tg_username=tg_username, school_grade=school_grade,
        )

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
            "sdek_address": sdek_address,
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
    db_user.sdek_address = sdek_address
    db_user.university_year = parsed_university_year
    if tg_mismatch_after_save is not None:
        db_user.tg_username_mismatch = tg_mismatch_after_save
    db.commit()
    # Иначе Redis продолжит отдавать старые контакты на всех экранах ученика.
    invalidate_session(user["session_id"])

    return RedirectResponse("/cabinet/personal?saved=1", status_code=302)


def _save_holiday_contacts(
    request: Request, user: dict, db: DBSession, *,
    first_name: str, last_name: str, phone: str, tg_username: str,
    school_grade: str,
):
    """«Контакты» каникулярного набора: поля его короткой анкеты, без
    родителя, СДЭК, вуза, почты и города. Ник обязателен (владелец
    17.10.2026) и берётся только из Telegram, форма его не меняет
    (`cabinet_student.holiday_tg_username`, 23.10.2026). Ночная сверка
    (`exam_scheduler`) каникулярных не проверяет. Пояс — московский
    (`HOLIDAY_TIMEZONE`). Начало обучения не трогаем — его поставила анкета."""
    first_name = first_name.strip()
    last_name = last_name.strip()
    phone = normalize_phone(phone)
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    tg_username = holiday_tg_username(db_user)
    grade = parse_school_grade(school_grade)

    errors = validate_holiday_profile(
        db, user["user_id"], first_name=first_name, last_name=last_name,
        phone=phone, tg_username=tg_username, grade=grade,
    )
    if errors:
        form = {
            "first_name": first_name, "last_name": last_name, "phone": phone,
            "tg_username": tg_username, "school_grade": grade,
        }
        return templates.TemplateResponse(request, "cabinet_personal_contacts.html",
            _contacts_ctx(request, user, errors=errors, form=form),
        )

    db_user.first_name = first_name
    db_user.last_name = last_name
    db_user.name = f"{first_name} {last_name}"
    db_user.timezone = HOLIDAY_TIMEZONE
    db_user.phone = phone
    db_user.tg_username = tg_username
    db_user.school_grade = grade
    ensure_grade_tag(db, db_user)
    db.commit()
    invalidate_session(user["session_id"])
    return RedirectResponse("/cabinet/personal?saved=1", status_code=302)
