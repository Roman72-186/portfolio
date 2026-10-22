"""Рассылки — сеть: проверка у преподавателя и отправка ученикам.

Правила — в `services/broadcasts.py`, здесь только доставка. Два требования
из `docs/invariants/notifications.md`, без которых пачка кладёт воркер:

- **база на время сети закрыта.** Данные читаются короткой сессией и
  копируются, итоги пишутся своей короткой сессией пачками;
- **не больше `NOTIFY_CONCURRENCY` отправок разом**, у каждого запроса тайм-аут.

**Файл грузится в Telegram один раз.** На проверке у преподавателя вложение
уходит файлом, Telegram возвращает `file_id`, и ученикам сообщение идёт по
нему — с нашего РФ-сервера через прокси не тянется сотня копий кружка.

Отправка берёт только строки журнала «ждёт», чьё время пришло (`due_at`), и
отмечает итог по каждой, поэтому прерванный проход (перезапуск приложения)
досылается без повторов. Перед сетью проход заводит копии в колокольчике
(`broadcasts.create_bells`) — той же короткой сессией, что читает журнал.

Отложенные рассылки запускает планировщик (`run_due_broadcasts`, раз в
минуту): заводит журнал запланированных и берёт следующую волну «у ученика».
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_, update

from app.cache import invalidate_unread
from app.config import settings
from app.db.database import SessionLocal
from app.models.broadcast import (
    MEDIA_PHOTO,
    MEDIA_VIDEO_NOTE,
    MEDIA_VOICE,
    RECIPIENT_FAILED,
    RECIPIENT_NO_TELEGRAM,
    RECIPIENT_PENDING,
    RECIPIENT_SENT,
    STATUS_DRAFT,
    STATUS_SENDING,
    Broadcast,
    BroadcastRecipient,
)
from app.models.user import User
from app.services import broadcasts as bc
from app.services import s3 as s3_service
from app.services import telegram as telegram_service
from app.services.notify import NOTIFY_CONCURRENCY

logger = logging.getLogger(__name__)

# Метод Bot API и поле файла для каждого вида вложения.
_MEDIA_METHODS = {
    MEDIA_PHOTO: ("sendPhoto", "photo"),
    MEDIA_VOICE: ("sendVoice", "voice"),
    MEDIA_VIDEO_NOTE: ("sendVideoNote", "video_note"),
}
# Итоги пишутся в базу пачками: прерванный проход теряет не больше пачки.
FLUSH_EVERY = 20
# Сколько раз ждать по 429 «слишком часто» и сколько максимум спать за раз.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_SLEEP = 30
# Кнопка «Отправить» под проверкой была до 14.10.2026 — у старых проверок в
# чатах она осталась. Нажатие ничего не отправляет, только отвечает, где
# теперь отправка (владелец: «подтверждение только на стороне платформы»).
CALLBACK_PREFIX = "bc"
OLD_BUTTON_ANSWER = "Рассылка теперь отправляется только на сайте"


@dataclass
class _Content:
    """Копия полей рассылки — сеть идёт без сессии."""
    broadcast_id: int
    text: str
    media_kind: str | None
    media_s3_path: str | None
    media_filename: str | None
    media_content_type: str | None
    file_id: str | None


@dataclass
class Delivered:
    ok: bool
    error: str = ""
    file_id: str | None = None
    message_ids: list[int] = field(default_factory=list)


def _copy_content(broadcast: Broadcast) -> _Content:
    return _Content(
        broadcast_id=broadcast.id,
        text=broadcast.text or "",
        media_kind=broadcast.media_kind,
        media_s3_path=broadcast.media_s3_path,
        media_filename=broadcast.media_filename,
        media_content_type=broadcast.media_content_type,
        file_id=broadcast.telegram_file_id,
    )


async def _call(method: str, data: dict, files: dict | None = None) -> telegram_service.ApiResult:
    """Вызов Bot API с ожиданием по 429: при рассылке пачкой Telegram может
    попросить подождать, и это не повод записать ученику «не дошло»."""
    result = await telegram_service.call_api(method, data, files=files)
    attempt = 0
    while not result.ok and result.status == 429 and attempt < RATE_LIMIT_RETRIES:
        attempt += 1
        await asyncio.sleep(min(result.retry_after or 1, RATE_LIMIT_MAX_SLEEP))
        result = await telegram_service.call_api(method, data, files=files)
    return result


def _file_id(kind: str, message: dict | None) -> str | None:
    if not isinstance(message, dict):
        return None
    if kind == MEDIA_PHOTO:
        sizes = message.get("photo") or []
        return sizes[-1].get("file_id") if sizes else None
    return (message.get(kind) or {}).get("file_id")


def _error_text(result: telegram_service.ApiResult) -> str:
    """Причина для журнала — словами, которые поймёт служба заботы."""
    description = result.description.lower()
    if result.status == 403:
        return "Ученик заблокировал бота"
    if "chat not found" in description:
        return "Чат с учеником не найден"
    if "can't parse entities" in description:
        return "Telegram не принял оформление текста"
    if result.status is None:
        return result.description or "Нет связи с Telegram"
    return result.description[:300] or f"Ошибка Telegram {result.status}"


async def deliver(
    chat_id: int, content: _Content, *, upload: bytes | None = None,
    reply_markup: dict | None = None,
) -> Delivered:
    """Одно сообщение рассылки в один чат — ровно как его увидит ученик.

    `upload` — байты вложения, если `file_id` ещё нет. У кружка подписи в
    Telegram нет: текст уходит следом отдельным сообщением.
    """
    delivered = Delivered(ok=True)
    text = content.text
    trailing_text = text
    if content.media_kind:
        method, field_name = _MEDIA_METHODS[content.media_kind]
        data: dict = {"chat_id": chat_id}
        files = None
        if content.file_id:
            data[field_name] = content.file_id
        elif upload is not None:
            files = {field_name: (
                content.media_filename or field_name, upload,
                content.media_content_type or "application/octet-stream",
            )}
        else:
            return Delivered(ok=False, error="Файл вложения недоступен")
        if content.media_kind != MEDIA_VIDEO_NOTE and text:
            data["caption"] = text
            data["parse_mode"] = "HTML"
            trailing_text = ""
        if reply_markup is not None and not trailing_text:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        result = await _call(method, data, files)
        if not result.ok:
            return Delivered(ok=False, error=_error_text(result))
        delivered.file_id = _file_id(content.media_kind, result.result) or content.file_id
        if isinstance(result.result, dict) and result.result.get("message_id"):
            delivered.message_ids.append(result.result["message_id"])
    if trailing_text:
        data = {"chat_id": chat_id, "text": trailing_text, "parse_mode": "HTML"}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        result = await _call("sendMessage", data)
        if not result.ok:
            return Delivered(ok=False, error=_error_text(result), file_id=delivered.file_id)
        if isinstance(result.result, dict) and result.result.get("message_id"):
            delivered.message_ids.append(result.result["message_id"])
    return delivered


async def _media_bytes(content: _Content) -> bytes | None:
    if not content.media_kind or content.file_id or not content.media_s3_path:
        return None
    return await asyncio.to_thread(s3_service.download_from_s3, content.media_s3_path)


def edit_url(broadcast_id: int) -> str:
    return f"https://{settings.domain}/cabinet/staff/broadcasts/{broadcast_id}"


# ── Проверка у преподавателя ────────────────────────────────────────────────


@dataclass
class _PreviewJob:
    content: _Content
    chat_id: int
    fingerprint: str
    note: str


def _load_preview(broadcast_id: int, sender_id: int, target_id: int | None) -> _PreviewJob | str:
    db = SessionLocal()
    try:
        broadcast = db.get(Broadcast, broadcast_id)
        if broadcast is None:
            return "Рассылки нет"
        if broadcast.status != STATUS_DRAFT:
            return "Эта рассылка уже отправлена"
        if not (broadcast.text or broadcast.media_kind):
            return "Сообщение пустое: добавьте текст или вложение"
        problem = bc.text_problem(broadcast)
        if problem:
            return problem
        audience = bc.get_audience(db, broadcast.id)
        if audience.is_everyone:
            return "Не выбрано, кому отправить"
        target = bc.preview_target(db, sender_id, target_id)
        if target is None:
            return "Ни у вас, ни у служебных аккаунтов нет Telegram — проверку некуда прислать"
        users = bc.audience_users(db, audience)
        summary = bc.summarize(users)
        lines = [
            "☝️ Так сообщение увидят ученики.",
            f"Кому: {bc.audience_text(audience, bc.audience_names(db, audience))}.",
            f"Получат: {summary.reachable} из {summary.total}.",
        ]
        if summary.no_telegram:
            lines.append(f"Не подключили бота: {summary.no_telegram}.")
        if summary.notifications_off:
            lines.append(f"Выключили уведомления: {summary.notifications_off}.")
        lines.append(f"Когда: {bc.schedule_text(broadcast)}.")
        lines.append(f"Отправить или поправить — на сайте: {edit_url(broadcast_id)}")
        problem = bc.schedule_problem(broadcast, users)
        if problem:
            return problem
        return _PreviewJob(
            content=_copy_content(broadcast),
            chat_id=target[1],
            fingerprint=bc.fingerprint(broadcast, audience),
            note="\n".join(lines),
        )
    finally:
        db.close()


async def send_preview(broadcast_id: int, sender_id: int, target_id: int | None = None) -> str | None:
    """Прислать преподавателю сообщение ровно в том виде, в каком его получат
    ученики, и под ним — кому уйдёт и ссылку на экран рассылки. Кнопки
    «Отправить» в Telegram нет: отправляют и правят только на сайте
    (владелец 14.10.2026).

    Возвращает текст ошибки для экрана или None, если проверка ушла.
    """
    job = _load_preview(broadcast_id, sender_id, target_id)
    if isinstance(job, str):
        return job
    upload = await _media_bytes(job.content)
    if job.content.media_kind and not job.content.file_id and upload is None:
        return "Не удалось взять вложение из хранилища — загрузите его заново"

    delivered = await deliver(job.chat_id, job.content, upload=upload)
    if not delivered.ok:
        return f"Telegram не принял сообщение: {delivered.error}"

    note = await _call("sendMessage", {
        "chat_id": job.chat_id,
        "text": job.note,
        # Без карточки сайта под ссылкой: подпись должна читаться целиком.
        "link_preview_options": json.dumps({"is_disabled": True}),
    })
    if not note.ok:
        logger.warning("broadcast %s: подпись проверки не ушла: %s", broadcast_id, note.description)

    db = SessionLocal()
    try:
        broadcast = db.get(Broadcast, broadcast_id)
        # Пока сообщение летело, черновик могли поправить: тогда проверенной
        # считать нечего — отпечаток уже другой.
        if broadcast is None or broadcast.status != STATUS_DRAFT:
            return "Рассылку успели отправить или удалить"
        if bc.fingerprint(broadcast, bc.get_audience(db, broadcast_id)) != job.fingerprint:
            return "Сообщение поменялось, пока шла проверка — пришлите её заново"
        broadcast.preview_fingerprint = job.fingerprint
        broadcast.preview_chat_id = job.chat_id
        broadcast.preview_sent_at = datetime.now(timezone.utc)
        if delivered.file_id:
            broadcast.telegram_file_id = delivered.file_id
        db.commit()
    finally:
        db.close()
    return None


# ── Отправка ученикам ───────────────────────────────────────────────────────


@dataclass
class _Target:
    recipient_id: int
    chat_id: int | None


def _load_run(broadcast_id: int) -> tuple[_Content, list[_Target]] | None:
    """Копия рассылки и кому слать сейчас. Попутно — копии в колокольчике
    всем, чьё время пришло: это база, а не сеть, и делается до неё."""
    db = SessionLocal()
    try:
        broadcast = db.get(Broadcast, broadcast_id)
        if broadcast is None or broadcast.status != STATUS_SENDING:
            return None
        now = datetime.now(timezone.utc)
        belled = bc.create_bells(db, broadcast_id, now)
        db.commit()
        for user_id in belled:
            invalidate_unread(user_id)
        rows = (
            db.query(BroadcastRecipient.id, User.telegram_chat_id)
            .join(User, User.id == BroadcastRecipient.user_id)
            .filter(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == RECIPIENT_PENDING,
                or_(BroadcastRecipient.due_at.is_(None), BroadcastRecipient.due_at <= now),
            )
            .order_by(BroadcastRecipient.id)
            .all()
        )
        return _copy_content(broadcast), [_Target(rid, chat) for rid, chat in rows]
    finally:
        db.close()


def _flush(results: list[tuple[int, str, str | None]], file_id: str | None, broadcast_id: int) -> None:
    if not results and not file_id:
        return
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        for recipient_id, status, error in results:
            row = db.get(BroadcastRecipient, recipient_id)
            if row is None or row.status != RECIPIENT_PENDING:
                continue
            row.status = status
            row.error = error
            row.sent_at = now if status == RECIPIENT_SENT else None
        if file_id:
            broadcast = db.get(Broadcast, broadcast_id)
            if broadcast is not None and not broadcast.telegram_file_id:
                broadcast.telegram_file_id = file_id
        # Проход жив: «Дослать» и планировщик не возьмут его, пока идут пачки.
        db.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == STATUS_SENDING)
            .values(sending_started_at=now)
        )
        db.commit()
    finally:
        db.close()


async def run_broadcast(broadcast_id: int) -> None:
    """Разослать всем «ждёт». Не поднимает исключений — фоновая задача."""
    try:
        await _run(broadcast_id)
    except Exception:
        logger.exception("broadcast %s: сбой рассылки", broadcast_id)


async def _run(broadcast_id: int) -> None:
    loaded = _load_run(broadcast_id)
    if loaded is None:
        return
    content, targets = loaded
    results: list[tuple[int, str, str | None]] = []
    new_file_id: str | None = None

    async def send_one(target: _Target, upload: bytes | None = None) -> Delivered:
        if not target.chat_id:
            results.append((target.recipient_id, RECIPIENT_NO_TELEGRAM, None))
            return Delivered(ok=False)
        delivered = await deliver(target.chat_id, content, upload=upload)
        if delivered.ok:
            results.append((target.recipient_id, RECIPIENT_SENT, None))
        else:
            results.append((target.recipient_id, RECIPIENT_FAILED, delivered.error[:300]))
        return delivered

    # Без `file_id` (проверка его не получила) — первому загрузкой, дальше по
    # тому, что вернул Telegram. Обычно сюда не попадаем: отправка требует
    # проверки, а проверка и есть загрузка.
    queue = list(targets)
    if content.media_kind and not content.file_id:
        upload = await _media_bytes(content)
        while queue and not content.file_id:
            delivered = await send_one(queue.pop(0), upload)
            if delivered.file_id:
                content.file_id = new_file_id = delivered.file_id
        if not content.file_id:
            results.extend(
                (target.recipient_id, RECIPIENT_FAILED, "Файл вложения недоступен")
                for target in queue
            )
            queue = []

    semaphore = asyncio.Semaphore(NOTIFY_CONCURRENCY)

    async def guarded(target: _Target) -> None:
        async with semaphore:
            await send_one(target)
            if len(results) >= FLUSH_EVERY:
                batch = results[:]
                results.clear()
                _flush(batch, None, broadcast_id)

    await asyncio.gather(*(guarded(target) for target in queue))
    _flush(results, new_file_id, broadcast_id)

    db = SessionLocal()
    try:
        bc.finish_if_done(db, broadcast_id)
    finally:
        db.close()
    logger.info("broadcast %s: проход завершён, получателей в проходе %d", broadcast_id, len(targets))


# ── Кнопка «Отправить» под старыми проверками ───────────────────────────────


def is_old_preview_button(data: str | None) -> bool:
    return (data or "").startswith(f"{CALLBACK_PREFIX}:")


async def answer_callback(callback_id: str, text: str) -> None:
    await _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


# ── Отложенные: планировщик ─────────────────────────────────────────────────


async def run_due_broadcasts() -> None:
    """Раз в минуту из планировщика: завести журнал запланированных, чьё время
    пришло, и разослать волну, чьё время пришло. Проходы — по одному, чтобы
    разом шло не больше `NOTIFY_CONCURRENCY` отправок. Не поднимает исключений."""
    db = SessionLocal()
    try:
        fired = bc.fire_scheduled(db)
        claimed = bc.claim_due_runs(db)
    except Exception:
        logger.exception("broadcast: сбой планировщика рассылок")
        db.rollback()
        return
    finally:
        db.close()
    for broadcast_id in fired:
        logger.info("broadcast %s: время пришло, журнал заведён", broadcast_id)
    for broadcast_id in dict.fromkeys(fired + claimed):
        await run_broadcast(broadcast_id)
