"""Приём уведомлений Продамуса об оплате (`services/payments.py`).

`POST /payments/prodamus/webhook` — вне `/cabinet`: без сессии и CSRF, его
зовёт сервер Продамуса. Подлинность — подпись в заголовке `Sign` секретным
ключом платёжной страницы. Ключ не настроен — 503, как у вебхука Telegram.

Ответ 200 останавливает повторы Продамуса; неверная подпись — 403, тогда он
повторит позже (если ошиблись мы, повтор даст время починить).
"""
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db.database import get_db
from app.services import payments as payment_service
from app.services.notify import notify_many

logger = logging.getLogger(__name__)

router = APIRouter()


async def _signed_fields(request: Request) -> dict:
    """Подписанные поля формы — они же единственное, что идёт в разбор.

    Подпись Продамус считает по `$_POST`, а файловые части формы туда не
    попадают (в примере документации `products` приходит JSON-файлом). До
    13.10.2026 файлы читались целиком без предела и уходили в разбор рядом с
    подписанными полями (код-ревью 12.10.2026): неподписанное не должно
    влиять на зачисление, а сервис читает только текстовые поля — статус,
    номер заказа, сумму. Поэтому файлы не читаются вовсе.
    """
    form = await request.form()
    text_items = [(k, v) for k, v in form.multi_items() if isinstance(v, str)]
    return payment_service.nest_form(text_items)


@router.post("/payments/prodamus/webhook")
async def prodamus_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[DBSession, Depends(get_db)],
):
    key = settings.prodamus_secret_key
    if not key:
        return PlainTextResponse("payments are not configured", status_code=503)

    signed = await _signed_fields(request)
    signature = request.headers.get("Sign")
    if not payment_service.prodamus_verify(signed, key, signature):
        if payment_service.prodamus_verify(signed, key + "demo", signature):
            # Демо-платёж Продамуса подписан ключом с суффиксом demo намеренно:
            # доступ по нему не продлеваем.
            logger.info("Prodamus: демо-платёж по заказу %s", signed.get("order_num"))
            return PlainTextResponse("demo payment ignored")
        # Значения не пишем — там ФИО и телефон; по ключам видно, с чем
        # расходится алгоритм подписи.
        logger.warning(
            "Prodamus: неверная подпись (есть заголовок: %s), поля: %s",
            bool(signature), sorted(signed),
        )
        return PlainTextResponse("signature incorrect", status_code=403)

    now = datetime.now(timezone.utc)
    try:
        result = payment_service.process_webhook(db, signed, now)
        db.commit()
    except IntegrityError:
        # Тот же заказ Продамуса пришёл параллельно и уже записан — повтор.
        # Но только если платёж и правда оплачен: иначе сбой был другой, и 200
        # без зачисления оставил бы деньги без доступа — Продамус не повторил
        # бы уведомление (23.10.2026). Не 200 — Продамус повторит.
        db.rollback()
        if not payment_service.webhook_order_paid(db, signed):
            logger.error("Prodamus: заказ %s не зачислен из-за ошибки базы — ждём повтора",
                         signed.get("order_num"))
            return PlainTextResponse("retry later", status_code=500)
        logger.info("Prodamus: повтор заказа %s", signed.get("order_id"))
        return PlainTextResponse("already processed")

    logger.info("Prodamus: заказ %s (%s) — %s",
                signed.get("order_num"), signed.get("order_id"), result.message)
    ids = [n.id for n in result.notifications]
    if ids:
        background_tasks.add_task(notify_many, ids)
    return PlainTextResponse(result.message, status_code=result.status_code)
