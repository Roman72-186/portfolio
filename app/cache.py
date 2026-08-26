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


def set_telegram_oidc_pkce(
    state: str,
    code_verifier: str,
    ttl: int = 600,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Store Telegram Login (OIDC) PKCE verifier server-side, mirrors set_vk_pkce.

    `extra` кладётся в тот же payload — так через state переносится назначение
    входа (`purpose`: обычный вход или гостевой пробник) и токен гостевой
    ссылки. Callback у обоих сценариев один и тот же — redirect_uri
    зарегистрирован в Telegram ровно один, второй туда не добавить.
    """
    if not _client:
        return False
    payload = json.dumps({
        "code_verifier": code_verifier,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    })
    try:
        _client.setex(f"tg_oidc_pkce:{state}", ttl, payload)
        return True
    except Exception:
        return False


def pop_telegram_oidc_pkce(state: str) -> dict[str, Any] | None:
    """Atomically read and delete Telegram OIDC PKCE verifier, mirrors pop_vk_pkce."""
    if not _client:
        return None
    try:
        pipe = _client.pipeline()
        pipe.get(f"tg_oidc_pkce:{state}")
        pipe.delete(f"tg_oidc_pkce:{state}")
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


def invalidate_unread(user_id: int) -> None:
    """Call after marking notifications read or creating new ones."""
    if not _client:
        return
    try:
        _client.delete(f"unread:{user_id}")
    except Exception:
        pass
