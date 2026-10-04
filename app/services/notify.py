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
# Страховка на одно уведомление целиком: Telegram с повторами, голосовое
# (скачать + перекодировать) и все push-подписки получателя.
NOTIFY_TIMEOUT_SEC = 180


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
        if user is None:
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
    if delivery.telegram_chat_id:
        await _send_telegram(delivery.telegram_chat_id, delivery.message)
    for target in delivery.push_targets:
        await _send_push(target, delivery.message)


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


async def _send_telegram(chat_id: int, notification: _Message) -> None:
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
                return
        # Голосовое не собралось или Telegram его не принял — ученик всё
        # равно получает уровень текстом, запись ждёт его в уведомлениях сайта.
    await telegram_service.send_message(chat_id, text)


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


async def _send_push(target: _PushTarget, notification: _Message) -> None:
    payload = json.dumps({
        "title": notification.title,
        "body": notification.text or "",
        "url": "/cabinet/notifications",
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
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
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
