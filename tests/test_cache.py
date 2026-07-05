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
