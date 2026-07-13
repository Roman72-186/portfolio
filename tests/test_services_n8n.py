"""Tests for app.services.n8n — photo upload to n8n webhook."""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.services.n8n as n8n_module
from app.services.n8n import send_photo_to_n8n

pytestmark = pytest.mark.usefixtures("enable_n8n")


@pytest.fixture(autouse=True)
def reset_n8n_client():
    """Reset the global httpx client before each test."""
    n8n_module._client = None
    yield
    n8n_module._client = None


def _call(user_id=123, name="Test", tariff="УВЕРЕННЫЙ", month="январь",
          photo_bytes=b"fake", filename="photo.jpg", work_id=None):
    return asyncio.run(send_photo_to_n8n(
        user_id=user_id,
        student_name=name,
        tariff=tariff,
        month=month,
        photo_bytes=photo_bytes,
        filename=filename,
        work_id=work_id,
    ))


def _make_mock_client(response_json: dict):
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_json
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_send_photo_success_returns_response():
    mock_client = _make_mock_client({"success": True, "drive_file_id": "abc123"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = _call(photo_bytes=b"\xff\xd8\xff fake jpeg")

    assert result["success"] is True
    assert result["drive_file_id"] == "abc123"


def test_send_photo_disabled_never_creates_http_client(monkeypatch):
    monkeypatch.setattr(n8n_module.settings, "n8n_enabled", False)
    client_factory = MagicMock()
    monkeypatch.setattr(n8n_module.httpx, "AsyncClient", client_factory)

    result = _call()

    assert result["success"] is False
    assert result["disabled"] is True
    client_factory.assert_not_called()


def test_send_photo_payload_structure():
    """Verify the JSON payload sent to n8n contains the expected fields."""
    mock_client = _make_mock_client({"success": True})
    photo_bytes = b"hello image"

    with patch("httpx.AsyncClient", return_value=mock_client):
        _call(
            user_id=555,
            name="Иванова Анна",
            tariff="МАКСИМУМ",
            month="март",
            photo_bytes=photo_bytes,
            filename="before.jpg",
            work_id=42,
        )

    _, kwargs = mock_client.post.call_args
    payload = kwargs["json"]

    assert payload["vk_id"] == 555
    assert payload["student_name"] == "Иванова Анна"
    assert payload["tariff"] == "МАКСИМУМ"
    assert payload["tariff_code"] == "01"          # МАКСИМУМ → 01
    assert payload["month"] == "март"
    assert payload["filename"] == "before.jpg"
    assert payload["source"] == "web_cabinet"
    assert payload["work_id"] == 42
    assert payload["idempotency_key"] == "work:42"
    assert payload["photo_base64"] == base64.b64encode(photo_bytes).decode()


def test_send_photo_adds_webhook_secret_header(monkeypatch):
    mock_client = _make_mock_client({"success": True})
    monkeypatch.setattr(n8n_module.settings, "n8n_webhook_secret", "secret-value")

    with patch("httpx.AsyncClient", return_value=mock_client):
        _call()

    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"]["X-Webhook-Secret"] == "secret-value"


def test_tariff_codes_mapped_correctly():
    """All three tariffs must map to the right numeric code."""
    cases = [
        ("МАКСИМУМ", "01"),
        ("УВЕРЕННЫЙ", "02"),
        ("Я С ВАМИ", "03"),
        ("UNKNOWN", "02"),   # unknown → default 02
    ]
    for tariff, expected_code in cases:
        mock_client = _make_mock_client({"success": True})
        with patch("httpx.AsyncClient", return_value=mock_client):
            n8n_module._client = None
            _call(tariff=tariff)

        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["tariff_code"] == expected_code, (
            f"tariff={tariff!r} should map to code {expected_code}"
        )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_send_photo_timeout_returns_error_dict():
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = _call()

    assert result["success"] is False
    assert "Таймаут" in result["error"] or "timeout" in result["error"].lower()


def test_send_photo_http_500_returns_error_dict():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)
    )

    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = _call()

    assert result["success"] is False
    assert "500" in result["error"]


def test_send_photo_unexpected_exception_returns_error_dict():
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = _call()

    assert result["success"] is False
    assert "boom" in result["error"]
