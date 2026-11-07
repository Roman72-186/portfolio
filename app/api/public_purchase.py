"""Публичный каталог и личная покупка до допуска в учебный кабинет."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_current_user, require_csrf
from app.limiter import limiter
from app.models.user import User
from app.models.payment import COHORT_FROM
from app.services import payments, public_purchase, telegram
from app.services.tz import _as_utc
from app.tmpl import templates

router = APIRouter()


@router.get("/tariffs")
def tariffs(request: Request, db: Annotated[Session, Depends(get_db)]):
    return templates.TemplateResponse(request, "public_tariffs.html", {
        "request": request, "tariffs": public_purchase.catalog(db),
        "sales_ready": public_purchase.sales_ready(), "rub_text": payments.rub_text,
    }, headers={"Cache-Control": "no-store"})


@router.get("/purchase/start")
@limiter.limit("20/minute")
def start(request: Request, db: Annotated[Session, Depends(get_db)], tariff: str = ""):
    if not public_purchase.sales_ready() or tariff not in {t["tariff"] for t in public_purchase.catalog(db)}:
        return RedirectResponse("/tariffs", status_code=302)
    from app.api.auth import telegram_oauth_redirect
    return telegram_oauth_redirect(purpose="purchase", purchase_tariff=tariff)


def _render(request, db, user, *, error=None):
    enrollment = public_purchase.enrollment_for(db, user["user_id"])
    if not enrollment:
        return RedirectResponse("/cabinet", status_code=302)
    if enrollment.activated_at:
        return RedirectResponse("/cabinet", status_code=302)
    student = db.get(User, user["user_id"])
    payment = public_purchase.confirmed_payment(db, enrollment)
    amount = (payment.amount_kop if payment else
              payments.reference_price_kop(db, enrollment.selected_tariff, COHORT_FROM))
    # Адрес приглашения даже не попадает в контекст до оплаты и анкеты.
    invitation = public_purchase.invite_url() if payment and student.profile_completed else ""
    return templates.TemplateResponse(request, "public_purchase.html", {
        "request": request, "user": user, "enrollment": enrollment,
        "payment": payment, "amount": amount, "rub_text": payments.rub_text,
        "period": payments.period_label(payment.period if payment else payments.due_period(student, datetime.now(timezone.utc))),
        "sales_ready": public_purchase.sales_ready(), "invite_url": invitation,
        "after_payment_text": public_purchase.AFTER_PAYMENT_TEXT, "error": error,
        "paid_expired": bool(payment and (student.paid_until is None
                              or _as_utc(student.paid_until) <= datetime.now(timezone.utc))),
    }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/purchase")
def purchase(request: Request, user: Annotated[dict, Depends(get_current_user)],
             db: Annotated[Session, Depends(get_db)]):
    return _render(request, db, user)


@router.post("/purchase/pay")
def pay(request: Request, user: Annotated[dict, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)], _csrf: Annotated[None, Depends(require_csrf)]):
    from app.api.auth import _public_base_url
    try:
        payment = public_purchase.checkout(db, db.get(User, user["user_id"]),
                                           _public_base_url(request), datetime.now(timezone.utc))
        db.commit()
    except payments.PaymentUnavailable as exc:
        db.rollback()
        return _render(request, db, user, error=str(exc))
    return RedirectResponse(payment.link_url, status_code=303)


@router.post("/purchase/check-channel")
@limiter.limit("10/minute")
async def check_channel(request: Request, user: Annotated[dict, Depends(get_current_user)],
                        db: Annotated[Session, Depends(get_db)],
                        _csrf: Annotated[None, Depends(require_csrf)]):
    enrollment = public_purchase.enrollment_for(db, user["user_id"])
    if not enrollment:
        return RedirectResponse("/cabinet", status_code=303)
    if enrollment.activated_at:
        return RedirectResponse("/cabinet", status_code=303)
    if not public_purchase.confirmed_payment(db, enrollment) or not user["profile_completed"]:
        return _render(request, db, user, error="Сначала оплати курс и заполни анкету.")
    member = await telegram.check_channel_membership(user["telegram_chat_id"])
    if member is not True:
        error = ("Пока не видим тебя в канале. Вступи по ссылке и проверь участие ещё раз."
                 if member is False else "Не удалось проверить участие. Попробуй ещё раз через минуту.")
        return _render(request, db, user, error=error)
    # После сетевого запроса заново читаем состояние под блокировкой:
    # возврат или блокировка аккаунта могли произойти, пока отвечал Telegram.
    student = db.query(User).filter(User.id == user["user_id"]).populate_existing().one()
    if not student.is_active or student.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Аккаунт недоступен")
    student.is_group_member = True
    db.flush()
    if not public_purchase.activate(db, student.id, datetime.now(timezone.utc)):
        db.rollback()
        return _render(request, db, user, error="Доступ пока не открыт. Проверь оплату или напиши в службу заботы.")
    db.commit()
    return RedirectResponse("/cabinet/learning?welcome=1", status_code=303)
