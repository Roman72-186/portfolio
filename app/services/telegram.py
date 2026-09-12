"""Прямая интеграция с Telegram Bot API — без n8n-посредника.

Используется для входа через бота (проверка членства в закрытом канале) и
для рассылки уведомлений. Стиль запросов намеренно повторяет services/vk.py:
общий persistent httpx-клиент, инициализация/закрытие через lifespan
приложения (app/main.py), общий request_with_retry для устойчивости к
временным сбоям API.
"""
import logging

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


async def send_message(chat_id: int, text: str, *, reply_markup: dict | None = None) -> bool:
    """Отправить сообщение пользователю. Ошибки не поднимает — логирует и
    возвращает False, включая случай, когда пользователь заблокировал бота
    (403): рассылка уведомлений не должна падать целиком из-за одного
    недоступного получателя."""
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


async def check_channel_membership(user_id: int) -> bool | None:
    """Проверить членство user_id в settings.telegram_channel_id.

    Возвращает True/False при определённом ответе Telegram, либо None, если
    проверку выполнить не удалось (сеть, HTTP-ошибка, ошибка API). Как и
    check_group_membership в vk.py, вызывающий код не должен трактовать None
    как подтверждённое отсутствие членства.
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
