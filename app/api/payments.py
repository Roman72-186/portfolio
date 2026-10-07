"""Приём уведомлений Продамуса об оплате (`services/payments.py`).

`POST /payments/prodamus/webhook` — вне `/cabinet`: без сессии и CSRF, его
зовёт сервер Продамуса. Подлинность — подпись в заголовке `Sign` секретным
ключом платёжной страницы. Ключ не настроен — 503, как у вебхука Telegram.

Ответ 200 останавливает повторы Продамуса; неверная подпись — 403, тогда он
повторит позже (если ошиблись мы, повтор даст время починить).
"""
import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from starlette.datastructures import UploadFile

from app.config import settings
from app.db.database import get_db
from app.services import payments as payment_service
from app.services.notify import notify_many

logger = logging.getLogger(__name__)

router = APIRouter()


async def _signed_fields(request: Request) -> tuple[dict, dict]:
    """Поля формы для подписи и полный набор для разбора.

    Подпись Продамус считает по `$_POST`, а файловые части формы туда не
    попадают. В примере документации `products` приходит JSON-файлом — для
    подписи его не берём, для разбора читаем.
    """
    form = await request.form()
    text_items = [(k, v) for k, v in form.multi_items() if isinstance(v, str)]
    signed = payment_service.nest_form(text_items)
    full = dict(signed)
    for key, value in form.multi_items():
        if isinstance(value, UploadFile) and key not in full:
            try:
                full[key] = json.loads((await value.read()).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                full[key] = None
    return signed, full


@router.post("/payments/prodamus/webhook")
async def prodamus_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[DBSession, Depends(get_db)],
):
    key = settings.prodamus_secret_key
    if not key:
        return PlainTextResponse("payments are not configured", status_code=503)

    signed, full = await _signed_fields(request)
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
        result = payment_service.process_webhook(db, full, now)
        db.commit()
    except IntegrityError:
        # Тот же заказ Продамуса пришёл параллельно и уже записан — повтор.
        db.rollback()
        logger.info("Prodamus: повтор заказа %s", full.get("order_id"))
        return PlainTextResponse("already processed")

    logger.info("Prodamus: заказ %s (%s) — %s",
                full.get("order_num"), full.get("order_id"), result.message)
    ids = [n.id for n in result.notifications]
    if ids:
        background_tasks.add_task(notify_many, ids)
    return PlainTextResponse(result.message, status_code=result.status_code)
