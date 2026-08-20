"""Единый диспетчер уведомлений (Фаза 4).

Рассылает уже сохранённое in-app Notification в оба внешних канала —
Telegram и Web Push — независимо друг от друга. Прямое требование
владельца с созвона 17.08: если ученик отключил push в браузере,
Telegram всё равно должен присылать уведомление (и наоборот) — поэтому
каналы не блокируют и не подменяют друг друга, отказ одного не влияет
на другой.

Вызывать ПОСЛЕ db.commit() кода, создавшего Notification (как фоновую
задачу FastAPI BackgroundTasks либо через notify_many_sync из потока
APScheduler) — сервис открывает свою сессию БД по notification_id и не
должен блокировать чужую транзакцию сетевыми запросами.
"""
import asyncio
import json
import logging

from pywebpush import WebPushException, webpush_async

from app.config import settings
from app.db.database import SessionLocal
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import telegram as telegram_service

logger = logging.getLogger(__name__)

# Статусы push-сервиса, означающие, что подписка протухла/отозвана и
# больше не годна — остальные ошибки временные, подписку не гасим.
_PUSH_GONE_STATUSES = {404, 410}


async def notify(notification_id: int) -> None:
    """Разослать уведомление notification_id во все доступные каналы получателя.

    Не поднимает исключений — вызывается как fire-and-forget фоновая задача,
    сбой одного получателя/канала не должен ронять ничего вокруг.
    """
    db = SessionLocal()
    try:
        notification = db.get(Notification, notification_id)
        if notification is None:
            return
        user = db.get(User, notification.user_id)
        if user is None:
            return

        if user.telegram_chat_id and user.telegram_notifications_enabled:
            await _send_telegram(user.telegram_chat_id, notification)

        if settings.vapid_private_key:
            subs = (
                db.query(PushSubscription)
                .filter(
                    PushSubscription.user_id == user.id,
                    PushSubscription.is_active == True,  # noqa: E712
                )
                .all()
            )
            for sub in subs:
                await _send_push(db, sub, notification)
    except Exception:
        logger.exception("notify: сбой рассылки notification_id=%s", notification_id)
    finally:
        db.close()


async def notify_many(notification_ids: list[int]) -> None:
    """Разослать несколько уведомлений параллельно (пакетные сценарии)."""
    if not notification_ids:
        return
    await asyncio.gather(*(notify(nid) for nid in notification_ids))


def notify_many_sync(notification_ids: list[int]) -> None:
    """Синхронная обёртка для вызова вне event loop — APScheduler BackgroundScheduler
    крутит свои задачи в обычном потоке, там нет запущенного event loop."""
    if not notification_ids:
        return
    asyncio.run(notify_many(notification_ids))


async def _send_telegram(chat_id: int, notification: Notification) -> None:
    text = notification.title
    if notification.text:
        text = f"{text}\n\n{notification.text}"
    await telegram_service.send_message(chat_id, text)


async def _send_push(db, sub: PushSubscription, notification: Notification) -> None:
    payload = json.dumps({
        "title": notification.title,
        "body": notification.text or "",
        "url": "/cabinet/notifications",
    })
    try:
        await webpush_async(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth_key},
            },
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": f"mailto:{settings.vapid_claim_email}"},
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in _PUSH_GONE_STATUSES:
            sub.is_active = False
            db.commit()
        else:
            logger.warning(
                "Web push failed sub_id=%s status=%s: %s", sub.id, status, exc
            )
