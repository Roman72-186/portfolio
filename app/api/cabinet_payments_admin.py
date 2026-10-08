"""Экран «Оплаты»: кто оплатил за месяц, ручная отметка, отмена, возврат.

Расчёт — `services/payment_board.py`, деньги — `services/payments.py`
(`mark_paid_manually` → `record_paid`, `revert_paid`). Экран живёт в разделе
«Люди» (`section_access`, дерево `/cabinet/superadmin/payments`), записи —
действие `people:students`. Все адреса — `payments.STAFF_MIN_RANK`: по плану
ГП и суперадмин (ранг ≥ 4), пока владелец тестирует — только суперадмин.
Куратору и модератору ни экрана, ни кнопок (план, «Ручная отметка оплаты»).
"""
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import REPORT_EXCLUDED_USER_IDS
from app.db.database import get_db
from app.dependencies import require_csrf, require_role
from app.models.payment import STATUS_PAID, Payment
from app.models.role import Role
from app.models.user import User
from app.services import payment_board, payments
from app.services.notify import notify_many
from app.services.user_management import _invalidate_user_sessions
from app.services.tz import MSK_TZ
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/superadmin")

# Кому открыто — `payments.STAFF_MIN_RANK` (пока только суперадмин).
require_payments_staff = require_role(payments.STAFF_MIN_RANK)

STATE_LABELS = {
    payment_board.STATE_OVERDUE: ("Просрочено", "prg-badge--error"),
    payment_board.STATE_OPEN: ("Окно идёт", ""),
    payment_board.STATE_UPCOMING: ("Окно впереди", ""),
    payment_board.STATE_PAID: ("Оплачено", "prg-badge--done"),
}


@router.get("/payments", response_class=HTMLResponse)
def payments_board_page(
    request: Request,
    user: Annotated[dict, Depends(require_payments_staff)],
    db: Annotated[DBSession, Depends(get_db)],
    month: str = Query(""),
):
    now = datetime.now(timezone.utc)
    period = payment_board.parse_month(month, now)
    board = payment_board.month_board(db, period, now)
    current = payment_board.parse_month("", now)
    return templates.TemplateResponse(request, "superadmin_payments.html", {
        "request": request,
        "user": user,
        "board": board,
        "month_title": payments.period_label(period).capitalize(),
        "month_value": period.strftime("%Y-%m"),
        "prev_month": payments.add_months(period, -1).strftime("%Y-%m"),
        "next_month": payments.add_months(period, 1).strftime("%Y-%m"),
        "current_month": current.strftime("%Y-%m"),
        "is_current": period == current,
        "state_labels": STATE_LABELS,
        "today": now.astimezone(MSK_TZ).date().isoformat(),
        "rub": payments.rub_text,
        "rub_input": payments._rub_input,
        "display_name": payment_board.display_name,
        "tariff_text": payment_board.tariff_text,
    })


def _student(db: DBSession, student_id: int) -> User:
    student = (
        db.query(User)
        .join(Role, Role.id == User.role_id)
        .filter(User.id == student_id, Role.rank == 1, User.deleted_at.is_(None))
        .first()
    )
    if student is None or student.id in REPORT_EXCLUDED_USER_IDS:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    if student.archived_at is not None:
        raise HTTPException(status_code=409, detail="Ученик в архиве – оплату не меняем")
    return student


@router.post("/payments/mark")
def payments_mark(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_payments_staff)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    user_id: int = Form(...),
    month: str = Form(""),
    amount: str = Form(""),
    paid_on: str = Form(""),
    comment: str = Form(""),
):
    """«Отметить оплату» — перевод на расчётный счёт школы (JSON)."""
    student = _student(db, user_id)
    try:
        period = payments.parse_paid_month(month)
        if period is None:
            raise payments.PaymentSettingsError("Укажите месяц")
        amount_kop = payments.rub_str_to_kop(amount)
        if not amount_kop:
            raise payments.PaymentSettingsError("Укажите сумму")
        try:
            paid_date = date.fromisoformat(paid_on.strip())
        except ValueError:
            raise payments.PaymentSettingsError("Укажите дату поступления") from None
        payment, notifications = payments.mark_paid_manually(
            db, student, period=period, amount_kop=amount_kop, paid_on=paid_date,
            comment=comment, performed_by_id=user["user_id"],
        )
    except payments.PaymentSettingsError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    db.commit()
    _invalidate_user_sessions(db, student.id)
    ids = [n.id for n in notifications]
    if ids:
        background_tasks.add_task(notify_many, ids)
    return JSONResponse({"ok": True, "payment_id": payment.id})


def _paid_payment(db: DBSession, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None or payment.status != STATUS_PAID:
        raise HTTPException(status_code=404, detail="Оплата не найдена")
    _student(db, payment.user_id)
    return payment


def _revert(db: DBSession, user: dict, payment_id: int, *, refund: bool) -> JSONResponse:
    payment = _paid_payment(db, payment_id)
    try:
        payments.revert_paid(db, payment, refund=refund, performed_by_id=user["user_id"],
                             now=datetime.now(timezone.utc))
    except payments.PaymentSettingsError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    db.commit()
    _invalidate_user_sessions(db, payment.user_id)
    return JSONResponse({"ok": True})


@router.post("/payments/{payment_id}/cancel")
def payments_cancel_mark(
    payment_id: int,
    user: Annotated[dict, Depends(require_payments_staff)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Отменить ошибочную ручную отметку: срок вернётся к тому, что был до неё."""
    return _revert(db, user, payment_id, refund=False)


@router.post("/payments/{payment_id}/refund")
def payments_refund(
    payment_id: int,
    user: Annotated[dict, Depends(require_payments_staff)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """«Возврат оформлен»: деньги вернули в кабинете Продамуса руками, вебхука
    о возврате у Продамуса нет. Месяц снова не оплачен."""
    return _revert(db, user, payment_id, refund=True)
