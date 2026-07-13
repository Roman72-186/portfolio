"""Contract and isolation tests for the n8n-backed Drive gallery service."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.drive as drive_module

pytestmark = pytest.mark.usefixtures("enable_n8n")


@pytest.fixture(autouse=True)
def reset_drive_state():
    drive_module._client = None
    drive_module._photos_cache.clear()
    drive_module._failure_cache_until.clear()
    drive_module._file_index.clear()
    yield
    drive_module._client = None
    drive_module._photos_cache.clear()
    drive_module._failure_cache_until.clear()
    drive_module._file_index.clear()


def _response(photo_id: str = "drive-1") -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "photos": [{
            "id": photo_id,
            "name": "photo.jpg",
            "thumbnail": "https://drive.example/thumb",
            "download": "https://drive.example/view",
            "created": "2026-07-13T10:00:00Z",
            "type": "after",
        }]
    }
    return response


def test_list_photos_sends_stable_id_and_webhook_secret(monkeypatch):
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(return_value=_response())
    monkeypatch.setattr(drive_module, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(drive_module.settings, "n8n_webhook_secret", "test-secret")

    photos = asyncio.run(drive_module.list_student_photos(42, "anna"))

    assert photos[0]["id"] == "drive-1"
    _, kwargs = client.post.call_args
    assert kwargs["json"]["vk_id"] == 42
    assert kwargs["json"]["student_name"] == "anna"
    assert kwargs["headers"]["X-Webhook-Secret"] == "test-secret"
    assert kwargs["timeout"] == drive_module._UI_REQUEST_TIMEOUT


def test_list_photos_disabled_never_calls_n8n(monkeypatch):
    monkeypatch.setattr(drive_module.settings, "n8n_enabled", False)
    get_client = AsyncMock()
    monkeypatch.setattr(drive_module, "_get_client", get_client)

    assert asyncio.run(drive_module.list_student_photos(42, "anna")) == []
    get_client.assert_not_awaited()


def test_thumbnail_index_is_scoped_by_vk_id(monkeypatch):
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(return_value=_response("shared-file-id"))
    monkeypatch.setattr(drive_module, "_get_client", AsyncMock(return_value=client))

    asyncio.run(drive_module.list_student_photos(42, "anna"))

    assert drive_module.get_photo_thumbnail_url(42, "shared-file-id") == "https://drive.example/thumb"
    assert drive_module.get_photo_thumbnail_url(99, "shared-file-id") is None


def test_failed_list_call_is_short_cached(monkeypatch):
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(side_effect=RuntimeError("n8n unavailable"))
    monkeypatch.setattr(drive_module, "_get_client", AsyncMock(return_value=client))

    assert asyncio.run(drive_module.list_student_photos(42, "anna")) == []
    assert asyncio.run(drive_module.list_student_photos(42, "anna")) == []
    assert client.post.await_count == 1


def test_transient_failure_returns_stale_success(monkeypatch):
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(return_value=_response("cached-file"))
    monkeypatch.setattr(drive_module, "_get_client", AsyncMock(return_value=client))

    cached = asyncio.run(drive_module.list_student_photos(42, "anna"))
    drive_module._photos_cache[42] = (0, cached)  # force cache expiry
    client.post = AsyncMock(side_effect=RuntimeError("temporary outage"))

    assert asyncio.run(drive_module.list_student_photos(42, "anna")) == cached


def test_successful_refresh_removes_stale_thumbnail(monkeypatch):
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(return_value=_response("old-file"))
    monkeypatch.setattr(drive_module, "_get_client", AsyncMock(return_value=client))

    asyncio.run(drive_module.list_student_photos(42, "anna"))
    drive_module._photos_cache.clear()
    client.post = AsyncMock(return_value=_response("new-file"))
    asyncio.run(drive_module.list_student_photos(42, "anna"))

    assert drive_module.get_photo_thumbnail_url(42, "old-file") is None
    assert drive_module.get_photo_thumbnail_url(42, "new-file") == "https://drive.example/thumb"


def test_background_sync_uses_resilient_timeout(monkeypatch):
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(return_value=_response())
    monkeypatch.setattr(drive_module, "_get_client", AsyncMock(return_value=client))

    asyncio.run(drive_module.list_student_photos(42, "anna", background=True))

    _, kwargs = client.post.call_args
    assert kwargs["timeout"] == drive_module._BACKGROUND_REQUEST_TIMEOUT


def test_owner_mismatch_response_is_rejected(monkeypatch):
    response = _response("foreign-file")
    response.json.return_value["owner_vk_id"] = 99
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(drive_module, "_get_client", AsyncMock(return_value=client))

    assert asyncio.run(drive_module.list_student_photos(42, "anna")) == []
    assert drive_module.get_photo_thumbnail_url(42, "foreign-file") is None
