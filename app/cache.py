"""Redis cache helpers for session data and short-lived auth state."""
import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis as _redis_lib

from app.config import settings

log = logging.getLogger(__name__)



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


def _warn(operation: str, exc: Exception) -> None:
    """Ошибка Redis не роняет запрос, но и не молчит (код-ревью 28.09.2026,
    P2 № 14): без строки в журнале отказ Redis выглядел как «сессия не
    сбросилась» или «вход через Telegram не прошёл» без единой зацепки.
    Ключ не пишем — в нём id сессии или state входа."""
    log.warning("Redis %s: %s: %s", operation, type(exc).__name__, exc)


def invalidate_session(session_id: str) -> None:
    if not _client:
        return
    try:
        _client.delete(f"session:{session_id}")
    except Exception as exc:
        _warn("invalidate_session", exc)


def set_telegram_oidc_pkce(
    state: str,
    code_verifier: str,
    ttl: int = 600,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Store Telegram Login (OIDC) PKCE verifier server-side.

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
    except Exception as exc:
        _warn("set_telegram_oidc_pkce", exc)
        return False


def pop_telegram_oidc_pkce(state: str) -> dict[str, Any] | None:
    """Atomically read and delete Telegram OIDC PKCE verifier."""
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
    except Exception as exc:
        _warn("pop_telegram_oidc_pkce", exc)
        return None


# ── Unread notification count cache (TTL 60s) ─────────────────────────────────

UNREAD_TTL = 60  # seconds


def get_cached_unread(user_id: int) -> int | None:
    if not _client:
        return None
    try:
        raw = _client.get(f"unread:{user_id}")
        return int(raw) if raw is not None else None
    except Exception as exc:
        _warn("get_cached_unread", exc)
        return None


def set_cached_unread(user_id: int, count: int) -> None:
    if not _client:
        return
    try:
        _client.setex(f"unread:{user_id}", UNREAD_TTL, count)
    except Exception as exc:
        _warn("set_cached_unread", exc)


def invalidate_unread(user_id: int) -> None:
    """Call after marking notifications read or creating new ones."""
    if not _client:
        return
    try:
        _client.delete(f"unread:{user_id}")
    except Exception as exc:
        _warn("invalidate_unread", exc)
