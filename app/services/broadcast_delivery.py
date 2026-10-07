"""Рассылки — сеть: проверка у преподавателя и отправка ученикам.

Правила — в `services/broadcasts.py`, здесь только доставка. Два требования
из `docs/invariants/notifications.md`, без которых пачка кладёт воркер:

- **база на время сети закрыта.** Данные читаются короткой сессией и
  копируются, итоги пишутся своей короткой сессией пачками;
- **не больше `NOTIFY_CONCURRENCY` отправок разом**, у каждого запроса тайм-аут.

**Файл грузится в Telegram один раз.** На проверке у преподавателя вложение
уходит файлом, Telegram возвращает `file_id`, и ученикам сообщение идёт по
нему — с нашего РФ-сервера через прокси не тянется сотня копий кружка.

Отправка берёт только строки журнала «ждёт» и отмечает итог по каждой, поэтому
прерванный проход (перезапуск приложения) досылается без повторов.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
CALLBACK_PREFIX = "bc"


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


def _ru_students(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        word = "ученикам"
    elif count % 10 == 1:
        word = "ученику"
    else:
        word = "ученикам"
    return f"{count} {word}"


# ── Проверка у преподавателя ────────────────────────────────────────────────


@dataclass
class _PreviewJob:
    content: _Content
    chat_id: int
    fingerprint: str
    token: str
    note: str
    reachable: int


def _load_preview(broadcast_id: int, sender_id: int) -> _PreviewJob | str:
    db = SessionLocal()
    try:
        broadcast = db.get(Broadcast, broadcast_id)
        if broadcast is None:
            return "Рассылки нет"
        if broadcast.status != STATUS_DRAFT:
            return "Эта рассылка уже отправлена"
        if not (broadcast.text or broadcast.media_kind):
            return "Сообщение пустое: добавьте текст или вложение"
        audience = bc.get_audience(db, broadcast.id)
        if audience.is_everyone:
            return "Не выбрано, кому отправить"
        target = bc.preview_target(db, sender_id)
        if target is None:
            return "К вашему аккаунту не привязан Telegram — проверку некуда прислать"
        users = bc.audience_users(db, audience)
        summary = bc.summarize(users)
        names = {
            user.id: " ".join(p for p in (user.first_name, user.last_name) if p) or f"ученик {user.id}"
            for user in db.query(User).filter(User.id.in_(audience.user_ids))
        } if audience.user_ids else {}
        lines = [
            "☝️ Так сообщение увидят ученики.",
            f"Кому: {bc.audience_text(audience, names)}.",
            f"Получат: {summary.reachable} из {summary.total}.",
        ]
        if summary.no_telegram:
            lines.append(f"Не подключили бота: {summary.no_telegram}.")
        if summary.notifications_off:
            lines.append(f"Выключили уведомления: {summary.notifications_off}.")
        return _PreviewJob(
            content=_copy_content(broadcast),
            chat_id=target[1],
            fingerprint=bc.fingerprint(broadcast, audience),
            token=bc.new_preview_token(),
            note="\n".join(lines),
            reachable=summary.reachable,
        )
    finally:
        db.close()


async def send_preview(broadcast_id: int, sender_id: int) -> str | None:
    """Прислать преподавателю сообщение ровно в том виде, в каком его получат
    ученики, и под ним — кому уйдёт и кнопки «Отправить» / «Исправить».

    Возвращает текст ошибки для экрана или None, если проверка ушла.
    """
    job = _load_preview(broadcast_id, sender_id)
    if isinstance(job, str):
        return job
    upload = await _media_bytes(job.content)
    if job.content.media_kind and not job.content.file_id and upload is None:
        return "Не удалось взять вложение из хранилища — загрузите его заново"

    delivered = await deliver(job.chat_id, job.content, upload=upload)
    if not delivered.ok:
        return f"Telegram не принял сообщение: {delivered.error}"

    buttons = [[{"text": "Исправить", "url": edit_url(broadcast_id)}]]
    if job.reachable:
        buttons.insert(0, [{
            "text": f"Отправить {_ru_students(job.reachable)}",
            "callback_data": f"{CALLBACK_PREFIX}:{broadcast_id}:{job.token}",
        }])
    note = await _call("sendMessage", {
        "chat_id": job.chat_id,
        "text": job.note,
        "reply_markup": json.dumps({"inline_keyboard": buttons}, ensure_ascii=False),
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
        broadcast.preview_token = job.token
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
    db = SessionLocal()
    try:
        broadcast = db.get(Broadcast, broadcast_id)
        if broadcast is None or broadcast.status != STATUS_SENDING:
            return None
        rows = (
            db.query(BroadcastRecipient.id, User.telegram_chat_id)
            .join(User, User.id == BroadcastRecipient.user_id)
            .filter(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == RECIPIENT_PENDING,
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


# ── Кнопка «Отправить» в Telegram ───────────────────────────────────────────


def parse_callback(data: str | None) -> tuple[int, str] | None:
    """`bc:<id>:<token>` → (id, token)."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


def approve_from_telegram(broadcast_id: int, token: str, chat_id: int) -> tuple[bool, str]:
    """Нажата «Отправить» под проверкой. Пускает, только если нажали в том
    чате, куда ушла проверка, токен этой проверки и версия не менялась.

    Возвращает (взяли ли в отправку, текст ответа на кнопку).
    """
    db = SessionLocal()
    try:
        broadcast = db.get(Broadcast, broadcast_id)
        if (
            broadcast is None
            or not broadcast.preview_token
            or broadcast.preview_token != token
            or broadcast.preview_chat_id != chat_id
        ):
            return False, "Эта проверка устарела — откройте рассылку на сайте"
        try:
            count = bc.start_sending(db, broadcast_id, actor_id=broadcast.created_by_id)
        except bc.SendRefused as exc:
            return False, str(exc)
        return True, f"Отправляю: {_ru_students(count)}"
    finally:
        db.close()


async def answer_callback(callback_id: str, text: str) -> None:
    await _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


async def close_preview_buttons(chat_id: int, message_id: int, text: str) -> None:
    """Под проверкой — итог вместо кнопок, чтобы второй раз не нажали."""
    await _call("editMessageReplyMarkup", {
        "chat_id": chat_id, "message_id": message_id,
        "reply_markup": json.dumps({"inline_keyboard": []}),
    })
    await _call("sendMessage", {"chat_id": chat_id, "text": text})
