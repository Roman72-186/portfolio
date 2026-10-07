"""Прямая интеграция с Telegram Bot API — без n8n-посредника.

Используется для входа через бота (проверка членства в закрытом канале) и
для рассылки уведомлений. Стиль запросов намеренно повторяет services/vk.py:
общий persistent httpx-клиент, инициализация/закрытие через lifespan
приложения (app/main.py), общий request_with_retry для устойчивости к
временным сбоям API.
"""
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.services._http import request_with_retry

logger = logging.getLogger(__name__)

# Статусы getChatMember, которые считаются подтверждённым членством в канале.
_MEMBER_STATUSES = {"member", "administrator", "creator"}

_client: httpx.AsyncClient | None = None


async def init_client() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=15.0)


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15.0)
    return _client


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


async def send_message(
    chat_id: int, text: str, *, reply_markup: dict | None = None,
    message_thread_id: int | None = None, reply_to_message_id: int | None = None,
) -> bool:
    """Отправить сообщение пользователю. Ошибки не поднимает — логирует и
    возвращает False, включая случай, когда пользователь заблокировал бота
    (403): рассылка уведомлений не должна падать целиком из-за одного
    недоступного получателя.

    `message_thread_id` — топик супергруппы, `reply_to_message_id` — ответ на
    сообщение (оба — служебный топик оценок ОС,
    `feedback_rating.send_rating_to_care_topic`)."""
    if not settings.telegram_bot_token:
        logger.warning("telegram.send_message: TELEGRAM_BOT_TOKEN не настроен")
        return False

    client = await _get_client()
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        # Одноразовые ссылки входа идут в этом же тексте — Telegram сам
        # открывает URL для карточки-превью почти сразу после отправки и
        # сжигает токен раньше, чем получатель успевает нажать (LinkPreviewOptions,
        # заменил disable_web_page_preview в Bot API с 2023-12-29).
        "link_preview_options": {"is_disabled": True},
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    if reply_to_message_id:
        # Сообщение, на которое отвечаем, могли успеть удалить — тогда пусть
        # уйдёт просто сообщением, а не ошибкой.
        payload["reply_parameters"] = {
            "message_id": reply_to_message_id, "allow_sending_without_reply": True,
        }

    try:
        resp = await request_with_retry(
            lambda: client.post(_api_url("sendMessage"), json=payload),
            label="Telegram sendMessage",
        )
    except Exception as exc:
        logger.warning("Telegram sendMessage failed chat_id=%s: %s", chat_id, exc)
        return False

    if resp.status_code == 403:
        logger.info("Telegram sendMessage: бот заблокирован chat_id=%s", chat_id)
        return False
    if resp.status_code >= 400:
        logger.warning(
            "Telegram sendMessage HTTP %s chat_id=%s body=%s",
            resp.status_code, chat_id, resp.text[:300],
        )
        return False
    return True


async def send_voice(
    chat_id: int, voice: bytes, *, filename: str, content_type: str, caption: str = "",
) -> bool:
    """Отправить голосовое загрузкой файла (multipart), а не ссылкой.

    Bot API, раздел «Sending by URL» (сверено 29.09.2026): по ссылке `sendVoice`
    рисует голосовое только для `audio/ogg` до 1 МБ, 1–20 МБ приходят файлом.
    Загрузкой голосовым идут OGG/Opus, MP3 и M4A до 50 МБ — поэтому байты
    передаёт вызывающий (`notify._send_telegram`). `caption` — до 1024
    символов, Telegram обрежет длиннее сам.

    Как и `send_message`, ошибок не поднимает — логирует и возвращает False,
    включая блокировку бота (403).
    """
    payload: dict = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption
    body = await _post_files(
        "sendVoice", chat_id, payload, {"voice": (filename, voice, content_type)},
    )
    return body is not None


async def send_photo(
    chat_id: int, photo: bytes, *, filename: str, caption: str = "",
    message_thread_id: int | None = None,
) -> int | None:
    """Отправить фото загрузкой файла (multipart). Ссылкой не отдаём: с нашего
    S3 Telegram скачивает не всегда. `caption` — HTML, до 1024 символов после
    разбора: длину проверяет вызывающий.

    Возвращает `message_id` отправленного сообщения или None при любой
    ошибке — как `send_message`, ничего не поднимает."""
    payload = _media_payload(chat_id, message_thread_id)
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    body = await _post_files("sendPhoto", chat_id, payload, {"photo": (filename, photo, "image/jpeg")})
    return _first_message_id(body)


async def send_media_group(
    chat_id: int, photos: list[tuple[str, bytes]], *, caption: str = "",
    message_thread_id: int | None = None,
) -> int | None:
    """Альбом из 2–10 фото загрузкой файлов: каждое — частью multipart под
    своим именем, в `media` на него ссылается `attach://<имя>`. Подпись
    Telegram показывает под альбомом, если она есть только у первого фото.

    Возвращает `message_id` первого сообщения альбома (на него можно
    ответить) или None при любой ошибке."""
    media = []
    files = {}
    for index, (filename, data) in enumerate(photos):
        name = f"photo{index}"
        item: dict = {"type": "photo", "media": f"attach://{name}"}
        if index == 0 and caption:
            item["caption"] = caption
            item["parse_mode"] = "HTML"
        media.append(item)
        files[name] = (filename, data, "image/jpeg")
    payload = _media_payload(chat_id, message_thread_id)
    payload["media"] = json.dumps(media)
    body = await _post_files("sendMediaGroup", chat_id, payload, files)
    return _first_message_id(body)


def _media_payload(chat_id: int, message_thread_id: int | None) -> dict:
    payload: dict = {"chat_id": str(chat_id)}
    if message_thread_id:
        payload["message_thread_id"] = str(message_thread_id)
    return payload


def _first_message_id(body: dict | None) -> int | None:
    """`sendPhoto` отвечает одним Message, `sendMediaGroup` — их списком."""
    if body is None:
        return None
    result = body.get("result")
    if isinstance(result, list):
        result = result[0] if result else None
    return result.get("message_id") if isinstance(result, dict) else None


async def _post_files(method: str, chat_id: int, data: dict, files: dict) -> dict | None:
    """Общий multipart-запрос для методов с файлами. Тело ответа Telegram или
    None: нет токена, сеть, блокировка бота (403), любой другой отказ."""
    if not settings.telegram_bot_token:
        logger.warning("telegram.%s: TELEGRAM_BOT_TOKEN не настроен", method)
        return None

    client = await _get_client()
    try:
        resp = await request_with_retry(
            lambda: client.post(_api_url(method), data=data, files=files, timeout=60.0),
            label=f"Telegram {method}",
        )
    except Exception as exc:
        logger.warning("Telegram %s failed chat_id=%s: %s", method, chat_id, exc)
        return None

    if resp.status_code == 403:
        logger.info("Telegram %s: бот заблокирован chat_id=%s", method, chat_id)
        return None
    if resp.status_code >= 400:
        logger.warning(
            "Telegram %s HTTP %s chat_id=%s body=%s",
            method, resp.status_code, chat_id, resp.text[:300],
        )
        return None
    try:
        return resp.json()
    except ValueError:
        return {}


async def check_channel_membership(user_id: int) -> bool | None:
    """Проверить членство user_id в settings.telegram_channel_id.

    Возвращает True/False при определённом ответе Telegram, либо None, если
    проверку выполнить не удалось (сеть, HTTP-ошибка, ошибка API). Вызывающий
    код не должен трактовать None как подтверждённое отсутствие членства.
    """
    if not settings.telegram_bot_token or not settings.telegram_channel_id:
        logger.warning("check_channel_membership: bot token или channel id не настроены")
        return None

    client = await _get_client()
    try:
        resp = await request_with_retry(
            lambda: client.post(_api_url("getChatMember"), json={
                "chat_id": settings.telegram_channel_id,
                "user_id": user_id,
            }),
            label="Telegram getChatMember",
        )
    except Exception as exc:
        logger.warning("Telegram getChatMember request failed for user_id=%s: %s", user_id, exc)
        return None

    if resp.status_code >= 400:
        logger.warning(
            "Telegram getChatMember HTTP %s user_id=%s body=%s",
            resp.status_code, user_id, resp.text[:300],
        )
        return None

    data = resp.json()
    if not data.get("ok"):
        logger.warning("Telegram getChatMember error user_id=%s: %s", user_id, data)
        return None

    status = data.get("result", {}).get("status")
    logger.info("Telegram getChatMember user_id=%s -> status=%s", user_id, status)
    return status in _MEMBER_STATUSES


async def get_chat_username(
    chat_id: int, *, max_attempts: int = 3, timeout: float = 15.0,
) -> tuple[bool, str | None]:
    """Текущий публичный ник владельца chat_id по Bot API getChat.

    Возвращает (True, username) при успешном ответе — username сам может
    быть None, если у человека сейчас нет публичного ника, это тоже
    достоверный результат. (False, None) — запрос не удался (сеть, HTTP,
    ошибка API): как и в check_channel_membership, False здесь значит
    «не смогли узнать», а не «ника нет», и вызывающий код не должен менять
    состояние на основании такого ответа.

    `max_attempts`/`timeout` — по умолчанию как у остальных вызовов (3
    попытки, таймаут клиента), это годится для ночного фонового прогона.
    Интерактивный вызов из формы (cabinet_personal.py) передаёт меньшие
    значения: там до 3 повторов по 15 секунд означали бы почти минуту
    зависшей кнопки «Сохранить» вместо быстрого «не удалось проверить».
    """
    if not settings.telegram_bot_token:
        logger.warning("get_chat_username: TELEGRAM_BOT_TOKEN не настроен")
        return False, None

    client = await _get_client()
    try:
        resp = await request_with_retry(
            lambda: client.post(_api_url("getChat"), json={"chat_id": chat_id}, timeout=timeout),
            label="Telegram getChat",
            max_attempts=max_attempts,
        )
    except Exception as exc:
        logger.warning("Telegram getChat request failed for chat_id=%s: %s", chat_id, exc)
        return False, None

    if resp.status_code >= 400:
        logger.warning(
            "Telegram getChat HTTP %s chat_id=%s body=%s",
            resp.status_code, chat_id, resp.text[:300],
        )
        return False, None

    data = resp.json()
    if not data.get("ok"):
        logger.warning("Telegram getChat error chat_id=%s: %s", chat_id, data)
        return False, None

    return True, data.get("result", {}).get("username")


@dataclass(frozen=True)
class ApiResult:
    """Ответ Bot API целиком — для рассылок (`services/broadcast_delivery.py`).

    Остальные функции модуля сворачивают ответ в True/False/None, а рассылке
    нужно больше: `file_id` загруженного файла (ученикам он уходит без
    повторной загрузки), причина отказа для журнала и `retry_after` при 429.
    `status` — HTTP-код, `None` — до Telegram не дошли (сеть, нет токена).
    """
    ok: bool
    status: int | None = None
    result: Any = None
    description: str = ""
    retry_after: int | None = None


async def call_api(
    method: str, data: dict, *, files: dict | None = None, timeout: float = 60.0,
) -> ApiResult:
    """Вызвать метод Bot API формой (multipart, если есть `files`).

    Значения `data` уходят полями формы: вложенные объекты (`reply_markup`)
    вызывающий передаёт уже строкой JSON. Ошибок не поднимает.
    """
    if not settings.telegram_bot_token:
        logger.warning("telegram.%s: TELEGRAM_BOT_TOKEN не настроен", method)
        return ApiResult(ok=False, description="Бот не настроен")

    client = await _get_client()
    form = {key: str(value) for key, value in data.items() if value is not None}
    try:
        resp = await request_with_retry(
            lambda: client.post(_api_url(method), data=form, files=files, timeout=timeout),
            label=f"Telegram {method}",
        )
    except Exception as exc:
        logger.warning("Telegram %s failed: %s", method, exc)
        return ApiResult(ok=False, description="Нет связи с Telegram")

    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code < 400 and body.get("ok"):
        return ApiResult(ok=True, status=resp.status_code, result=body.get("result"))
    parameters = body.get("parameters") or {}
    if resp.status_code != 403:
        logger.warning(
            "Telegram %s HTTP %s body=%s", method, resp.status_code, resp.text[:300],
        )
    return ApiResult(
        ok=False,
        status=resp.status_code,
        description=str(body.get("description") or f"HTTP {resp.status_code}"),
        retry_after=parameters.get("retry_after"),
    )
