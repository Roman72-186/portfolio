"""Redis cache helpers for session data and short-lived auth state."""
import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis as _redis_lib

from app.config import settings

log = logging.getLogger(__name__)

VK_PKCE_TTL = 300  # 5 minutes


def _get_client() -> _redis_lib.Redis | None:
    """Return a Redis client, or None if Redis is unavailable."""
    try:
        client = _redis_lib.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return client
    except Exception:
        return None


# Module-level client — created once, reused across requests.
# Falls back to None if Redis is not available (app works without cache).
try:
    _client: _redis_lib.Redis | None = _redis_lib.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=True,
    )
    _client.ping()
except Exception:
    log.warning("Redis unavailable — session caching disabled")
    _client = None


def invalidate_session(session_id: str) -> None:
    if not _client:
        return
    try:
        _client.delete(f"session:{session_id}")
    except Exception:
        pass


def set_vk_pkce(state: str, code_verifier: str, ttl: int = VK_PKCE_TTL) -> bool:
    """Store VK PKCE verifier server-side so mobile app handoff survives."""
    if not _client:
        return False
    payload = json.dumps({
        "code_verifier": code_verifier,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        _client.setex(f"vk_pkce:{state}", ttl, payload)
        return True
    except Exception:
        return False


def pop_vk_pkce(state: str) -> dict[str, Any] | None:
    """Atomically read and delete VK PKCE verifier for one-time callback use."""
    if not _client:
        return None
    try:
        pipe = _client.pipeline()
        pipe.get(f"vk_pkce:{state}")
        pipe.delete(f"vk_pkce:{state}")
        raw, _ = pipe.execute()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


# ── Unread notification count cache (TTL 60s) ─────────────────────────────────

UNREAD_TTL = 60  # seconds


def get_cached_unread(user_id: int) -> int | None:
    if not _client:
        return None
    try:
        raw = _client.get(f"unread:{user_id}")
        return int(raw) if raw is not None else None
    except Exception:
        return None


def set_cached_unread(user_id: int, count: int) -> None:
    if not _client:
        return
    try:
        _client.setex(f"unread:{user_id}", UNREAD_TTL, count)
    except Exception:
        pass


# ── Telegram-бот: ожидание выбора тарифа между /start и нажатием кнопки ──────

TELEGRAM_SIGNUP_TTL = 600  # 10 минут — время на выбор тарифа после /start


def set_telegram_signup_state(chat_id: int, data: dict[str, Any], ttl: int = TELEGRAM_SIGNUP_TTL) -> bool:
    if not _client:
        return False
    try:
        _client.setex(f"tg_signup:{chat_id}", ttl, json.dumps(data))
        return True
    except Exception:
        return False


def pop_telegram_signup_state(chat_id: int) -> dict[str, Any] | None:
    """Атомарно прочитать и удалить состояние — кнопку тарифа можно нажать один раз."""
    if not _client:
        return None
    try:
        pipe = _client.pipeline()
        pipe.get(f"tg_signup:{chat_id}")
        pipe.delete(f"tg_signup:{chat_id}")
        raw, _ = pipe.execute()
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def invalidate_unread(user_id: int) -> None:
    """Call after marking notifications read or creating new ones."""
    if not _client:
        return
    try:
        _client.delete(f"unread:{user_id}")
    except Exception:
        pass
