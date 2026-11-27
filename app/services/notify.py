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

**Соединение с базой на время сети не держим** (прод-инцидент 04.10.2026).
Публикация видеоурока разослала 90 уведомлений разом: `notify_many` запускал
все параллельно, каждое открывало сессию, читало пользователя и с открытой
транзакцией ждало Telegram и push-сервис — у `webpush_async` не было тайм-аута.
Пул воркера (10 + 5 соединений) кончился за секунды, 15 сессий провисели
«idle in transaction» больше десяти минут, и всё, что попадало в этот воркер,
падало — включая сохранение заданий в конструкторе, пока приложение не
перезапустили. Теперь: данные читаются короткой сессией и копируются
(`_load_delivery`), сеть идёт без соединения, одновременно — не больше
`NOTIFY_CONCURRENCY` рассылок, у каждого сетевого вызова есть тайм-аут.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update

from pywebpush import WebPushException, webpush_async

from app.config import settings
from app.db.database import SessionLocal
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import media_transcode, s3 as s3_service, telegram as telegram_service

logger = logging.getLogger(__name__)

# Статусы push-сервиса, означающие, что подписка протухла/отозвана и
# больше не годна — остальные ошибки временные, подписку не гасим.
_PUSH_GONE_STATUSES = {404, 410}

# Сколько уведомлений пачки рассылается одновременно. Соединение с базой
# рассылка на время сети уже не держит, но и сотню одновременных запросов к
# Telegram (лимит ~30 сообщений в секунду) и push-сервисам слать незачем.
NOTIFY_CONCURRENCY = 5
# Тайм-аут одного push-запроса. Без него `webpush_async` ждал ответа минутами.
PUSH_TIMEOUT_SEC = 15
# Сколько push-сервис держит пуш для недоступного устройства. По умолчанию
# `pywebpush` шлёт TTL=0: push-сервис отдаёт такой пуш, только если устройство
# на связи прямо сейчас, спящий телефон его теряет.
PUSH_TTL_SEC = 24 * 60 * 60
# Страховка на одно уведомление целиком: Telegram с повторами, голосовое
# (скачать + перекодировать) и все push-подписки получателя.
NOTIFY_TIMEOUT_SEC = 180
TELEGRAM_MAX_ATTEMPTS = 8
TELEGRAM_LEASE_SEC = 600


@dataclass
class _Message:
    """Копия полей уведомления — `_send_telegram` читает их как у модели."""

    title: str
    text: str | None
    audio_url: str | None


@dataclass
class _PushTarget:
    id: int
    endpoint: str
    p256dh: str
    auth_key: str


@dataclass
class _Delivery:
    message: _Message
    telegram_chat_id: int | None = None
    push_targets: list[_PushTarget] = field(default_factory=list)


def _load_delivery(notification_id: int) -> _Delivery | None:
    """Всё, что нужно для отправки, — короткой сессией и копией полей.

    Сессия закрывается до первого сетевого запроса: см. докстринг модуля.
    """
    db = SessionLocal()
    try:
        notification = db.get(Notification, notification_id)
        if notification is None:
            return None
        user = db.get(User, notification.user_id)
        if user is None or not user.is_active or user.deleted_at is not None:
            return None
        delivery = _Delivery(message=_Message(
            title=notification.title,
            text=notification.text,
            audio_url=notification.audio_url,
        ))
        if user.telegram_chat_id and user.telegram_notifications_enabled:
            delivery.telegram_chat_id = user.telegram_chat_id
        if settings.vapid_private_key:
            delivery.push_targets = [
                _PushTarget(id=sub.id, endpoint=sub.endpoint, p256dh=sub.p256dh, auth_key=sub.auth_key)
                for sub in db.query(PushSubscription).filter(
                    PushSubscription.user_id == user.id,
                    PushSubscription.is_active == True,  # noqa: E712
                ).all()
            ]
        return delivery
    finally:
        db.close()


async def notify(notification_id: int) -> None:
    """Разослать уведомление notification_id во все доступные каналы получателя.

    Не поднимает исключений — вызывается как fire-and-forget фоновая задача,
    сбой одного получателя/канала не должен ронять ничего вокруг.
    """
    try:
        await asyncio.wait_for(_deliver(notification_id), timeout=NOTIFY_TIMEOUT_SEC)
    except Exception:
        logger.exception("notify: сбой рассылки notification_id=%s", notification_id)


async def _deliver(notification_id: int) -> None:
    delivery = _load_delivery(notification_id)
    if delivery is None:
        return
    await _deliver_telegram(notification_id, delivery)
    for target in delivery.push_targets:
        await _send_push(target)


def _claim_telegram(notification_id: int) -> int | None:
    """Atomic lease; release the DB connection before touching the network."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        claimed = db.execute(update(Notification).where(
            Notification.id == notification_id,
            Notification.telegram_attempts < TELEGRAM_MAX_ATTEMPTS,
            or_(
                Notification.telegram_status.is_(None),
                (Notification.telegram_status.in_(("pending", "sending")))
                & (Notification.telegram_next_attempt_at <= now),
            ),
        ).values(
            telegram_status="sending",
            telegram_attempts=Notification.telegram_attempts + 1,
            telegram_next_attempt_at=now + timedelta(seconds=TELEGRAM_LEASE_SEC),
        ))
        if not claimed.rowcount:
            db.rollback()
            return None
        attempt = db.query(Notification.telegram_attempts).filter(Notification.id == notification_id).scalar()
        db.commit()
        return attempt


def _finish_telegram(notification_id: int, attempt: int, ok: bool) -> None:
    now = datetime.now(timezone.utc)
    status = "sent" if ok else ("failed" if attempt >= TELEGRAM_MAX_ATTEMPTS else "pending")
    with SessionLocal() as db:
        db.execute(update(Notification).where(
            Notification.id == notification_id,
            Notification.telegram_status == "sending",
            Notification.telegram_attempts == attempt,
        ).values(
            telegram_status=status,
            telegram_sent_at=now if ok else None,
            telegram_next_attempt_at=(
                now + timedelta(seconds=min(3600, 60 * 2 ** (attempt - 1)))
                if status == "pending" else None
            ),
        ))
        db.commit()
    # No message body, chat id, API URL or exception text in delivery logs.
    logger.warning("Telegram delivery notification_id=%s attempt=%s status=%s", notification_id, attempt, status)


async def _deliver_telegram(notification_id: int, delivery: _Delivery | None) -> None:
    if delivery is None or not delivery.telegram_chat_id:
        with SessionLocal() as db:
            db.execute(update(Notification).where(
                Notification.id == notification_id,
                or_(
                    Notification.telegram_status == "pending",
                    (Notification.telegram_status == "sending")
                    & (Notification.telegram_next_attempt_at <= datetime.now(timezone.utc)),
                ),
            ).values(telegram_status="skipped", telegram_next_attempt_at=None))
            db.commit()
        return
    attempt = _claim_telegram(notification_id)
    if attempt is None:
        return
    try:
        ok = await _send_telegram(delivery.telegram_chat_id, delivery.message)
    except Exception as exc:
        logger.warning("Telegram delivery exception notification_id=%s type=%s", notification_id, type(exc).__name__)
        ok = False
    _finish_telegram(notification_id, attempt, bool(ok))


def retry_telegram_sync() -> None:
    """Retry only queued Telegram deliveries, never historical rows or Web Push."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        due = (
            Notification.telegram_status.in_(("pending", "sending")),
            Notification.telegram_next_attempt_at <= now,
        )
        # A crash on the final attempt must not leave an eternal sending lease.
        db.execute(update(Notification).where(
            *due, Notification.telegram_attempts >= TELEGRAM_MAX_ATTEMPTS,
        ).values(telegram_status="failed", telegram_next_attempt_at=None))
        ids = [row[0] for row in db.query(Notification.id).filter(
            *due, Notification.telegram_attempts < TELEGRAM_MAX_ATTEMPTS,
        ).order_by(Notification.telegram_next_attempt_at, Notification.id).limit(100).all()]
        db.commit()

    async def run():
        limit = asyncio.Semaphore(NOTIFY_CONCURRENCY)

        async def one(notification_id):
            async with limit:
                try:
                    await asyncio.wait_for(
                        _deliver_telegram(notification_id, _load_delivery(notification_id)),
                        timeout=NOTIFY_TIMEOUT_SEC,
                    )
                except Exception as exc:
                    logger.warning("Telegram retry notification_id=%s type=%s", notification_id, type(exc).__name__)

        await asyncio.gather(*(one(nid) for nid in ids))

    if ids:
        asyncio.run(run())


async def notify_many(notification_ids: list[int]) -> None:
    """Разослать несколько уведомлений, не больше `NOTIFY_CONCURRENCY` разом.

    До 04.10.2026 здесь был голый `gather` по всем — см. докстринг модуля.
    """
    if not notification_ids:
        return
    limit = asyncio.Semaphore(NOTIFY_CONCURRENCY)

    async def one(notification_id: int) -> None:
        async with limit:
            await notify(notification_id)

    await asyncio.gather(*(one(nid) for nid in notification_ids))


def notify_many_sync(notification_ids: list[int]) -> None:
    """Синхронная обёртка для вызова вне event loop — APScheduler BackgroundScheduler
    крутит свои задачи в обычном потоке, там нет запущенного event loop."""
    if not notification_ids:
        return
    asyncio.run(notify_many(notification_ids))


async def _send_telegram(chat_id: int, notification: _Message) -> bool:
    text = notification.title
    if notification.text:
        text = f"{text}\n\n{notification.text}"
    if notification.audio_url:
        voice = await _telegram_voice(notification.audio_url)
        if voice is not None:
            name, data, mime = voice
            if await telegram_service.send_voice(
                chat_id, data, filename=name, content_type=mime, caption=text,
            ):
                return True
        # Голосовое не собралось или Telegram его не принял — ученик всё
        # равно получает уровень текстом, запись ждёт его в уведомлениях сайта.
    return await telegram_service.send_message(chat_id, text)


async def _telegram_voice(audio_url: str) -> tuple[str, bytes, str] | None:
    """Голосовое из S3 → OGG/Opus для `sendVoice` загрузкой файла.

    Ссылкой отдавать нельзя: m4a по URL Telegram присылает файлом, а не
    голосовым (см. `telegram.send_voice`). Скачивание (boto) и ffmpeg
    синхронные — оба в поток, чтобы не держать event loop: сюда приходят и из
    BackgroundTasks, и из планировщика через `notify_many_sync`.
    """
    path = s3_service.s3_path_from_public_url(audio_url)
    if not path:
        logger.warning("notify: голосовое не из нашего S3, в Telegram не ушло: %s", audio_url)
        return None
    data = await asyncio.to_thread(s3_service.download_from_s3, path)
    if not data:
        return None
    name = path.rsplit("/", 1)[-1]
    return await asyncio.to_thread(media_transcode.telegram_voice, name, data, "audio/mp4")


PUSH_ENDPOINT_COOKIE = "push_device_endpoint"


def is_allowed_push_endpoint(endpoint: str) -> bool:
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        return (
            len(endpoint) <= 2048 and url.scheme == "https"
            and url.username is None and url.password is None
            and url.port in (None, 443) and bool(url.path) and not url.fragment
            and (
                host in {"fcm.googleapis.com", "android.googleapis.com", "web.push.apple.com",
                         "updates.push.services.mozilla.com"}
                or host.endswith((".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com"))
            )
        )
    except ValueError:
        return False


async def _send_push(target: _PushTarget) -> None:
    if not is_allowed_push_endpoint(target.endpoint):
        return
    payload = json.dumps({
        # A shared browser can still receive a queued push after an account switch.
        "title": "Apparchi",
        "body": "Новое уведомление в кабинете",
        # Полный адрес, а не путь: подписку держит браузер того сайта, где её
        # оформили, и путь он достраивает своим адресом. Подписки, заведённые
        # на apparchi.ru до переезда 11.10.2026, иначе открывали бы мёртвый
        # старый домен (`static/sw.js::notificationclick`).
        "url": f"https://{settings.domain}/cabinet/notifications",
    })
    try:
        await webpush_async(
            subscription_info={
                "endpoint": target.endpoint,
                "keys": {"p256dh": target.p256dh, "auth": target.auth_key},
            },
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": f"mailto:{settings.vapid_claim_email}"},
            timeout=PUSH_TIMEOUT_SEC,
            ttl=PUSH_TTL_SEC,
            headers={"Urgency": "high"},
        )
    except WebPushException as exc:
        # `webpush_async` кладёт сюда ответ aiohttp — код в `.status`;
        # `.status_code` — у синхронного `webpush` (requests).
        resp = exc.response
        status = getattr(resp, "status", None) or getattr(resp, "status_code", None)
        if status in _PUSH_GONE_STATUSES:
            _deactivate_subscription(target.id)
        else:
            logger.warning(
                "Web push failed sub_id=%s status=%s: %s", target.id, status, exc
            )
    except Exception as exc:
        # Тайм-аут и обрыв связи — временные: подписку не гасим, а остальные
        # подписки и каналы получателя из-за одной не должны отказать.
        logger.warning("Web push failed sub_id=%s: %r", target.id, exc)


def _deactivate_subscription(subscription_id: int) -> None:
    """Погасить протухшую подписку — своей короткой сессией."""
    db = SessionLocal()
    try:
        sub = db.get(PushSubscription, subscription_id)
        if sub is not None:
            sub.is_active = False
            db.commit()
    finally:
        db.close()
