"""Tests for app/cache.py — invalidate_session, Redis fallback."""
from unittest.mock import MagicMock, patch

from app.cache import invalidate_session


def test_invalidate_session_no_crash_when_no_redis():
    with patch("app.cache._client", None):
        invalidate_session("any-session-id")


def test_invalidate_session_calls_delete():
    mock_redis = MagicMock()
    with patch("app.cache._client", mock_redis):
        invalidate_session("sess123")
        mock_redis.delete.assert_called_once_with("session:sess123")


def test_invalidate_session_graceful_on_redis_error():
    mock_redis = MagicMock()
    mock_redis.delete.side_effect = Exception("Redis timeout")
    with patch("app.cache._client", mock_redis):
        invalidate_session("sess123")


# ── Код-ревью 28.09.2026, P2 № 14: ошибка Redis не должна быть немой ─────────

import logging

import pytest
import redis

from app import cache


def _broken_client():
    client = MagicMock()
    error = redis.exceptions.ConnectionError("Connection refused")
    for method in ("delete", "setex", "get"):
        getattr(client, method).side_effect = error
    client.pipeline.return_value.execute.side_effect = error
    return client


@pytest.mark.parametrize("call", [
    lambda: cache.invalidate_session("sess-secret-123"),
    lambda: cache.set_telegram_oidc_pkce("state-secret-123", "verifier"),
    lambda: cache.pop_telegram_oidc_pkce("state-secret-123"),
    lambda: cache.get_cached_unread(7),
    lambda: cache.set_cached_unread(7, 1),
    lambda: cache.invalidate_unread(7),
], ids=[
    "invalidate_session", "set_telegram_oidc_pkce",
    "pop_telegram_oidc_pkce", "get_cached_unread", "set_cached_unread", "invalidate_unread",
])
def test_redis_error_is_logged_without_the_key_and_does_not_raise(call, caplog):
    with patch("app.cache._client", _broken_client()), caplog.at_level(logging.WARNING, logger="app.cache"):
        call()

    records = [r for r in caplog.records if r.name == "app.cache"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "ConnectionError" in message
    # Ключ несёт id сессии или state входа — в журнал ему нельзя.
    assert "secret-123" not in message
