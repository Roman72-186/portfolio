"""Unit tests for Bunny Stream embed signing."""

import hashlib
from datetime import datetime, timezone

import httpx
import pytest

from app.config import settings
from app.services.bunny_stream import (
    BunnyStreamCreateUncertainError,
    BunnyStreamConfigError,
    build_tus_credentials,
    create_video,
    delete_video,
    build_signed_embed_url,
    is_bunny_stream_available,
)


VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"


def _configure_bunny(monkeypatch, *, token_key: str = "private-test-key") -> None:
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_video_id", VIDEO_ID)
    monkeypatch.setattr(settings, "bunny_stream_token_key", token_key)
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    monkeypatch.setattr(settings, "bunny_stream_api_key", "private-upload-api-key")


def test_build_signed_embed_url_uses_bunny_sha256_contract(monkeypatch):
    _configure_bunny(monkeypatch)
    now = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    expires = int(now.timestamp()) + 300
    expected_token = hashlib.sha256(
        f"private-test-key{VIDEO_ID}{expires}".encode("utf-8")
    ).hexdigest()

    url = build_signed_embed_url(now=now)

    assert url == (
        f"https://iframe.mediadelivery.net/embed/720058/{VIDEO_ID}"
        f"?token={expected_token}&expires={expires}&autoplay=false"
    )
    assert "private-test-key" not in url


def test_bunny_stream_is_unavailable_when_feature_is_disabled(monkeypatch):
    _configure_bunny(monkeypatch)
    monkeypatch.setattr(settings, "bunny_stream_enabled", False)

    assert is_bunny_stream_available() is False


def test_build_tus_credentials_uses_presigned_contract_without_api_key(monkeypatch):
    _configure_bunny(monkeypatch)
    credentials = build_tus_credentials(VIDEO_ID, now=1_786_000_000)
    expires = 1_786_086_400
    expected = hashlib.sha256(
        f"720058private-upload-api-key{expires}{VIDEO_ID}".encode("utf-8")
    ).hexdigest()

    assert credentials == {
        "endpoint": "https://video.bunnycdn.com/tusupload",
        "library_id": 720058,
        "video_id": VIDEO_ID,
        "authorization_expire": expires,
        "authorization_signature": expected,
    }
    assert "private-upload-api-key" not in repr(credentials)


def test_build_tus_credentials_respects_disabled_feature_flag(monkeypatch):
    _configure_bunny(monkeypatch)
    monkeypatch.setattr(settings, "bunny_stream_enabled", False)

    with pytest.raises(BunnyStreamConfigError, match="disabled"):
        build_tus_credentials(VIDEO_ID)


def test_create_video_keeps_stream_api_key_server_side(monkeypatch):
    _configure_bunny(monkeypatch)
    captured = {}

    class Response:
        status_code = 200
        content = b'{"guid":"35ed80ae-8103-4528-a700-3f69ec56957d"}'

        def json(self):
            return {"guid": VIDEO_ID}

    def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr("app.services.bunny_stream.httpx.request", fake_request)
    result = create_video("Урок")

    assert result["guid"] == VIDEO_ID
    assert captured["method"] == "POST"
    assert captured["url"] == "https://video.bunnycdn.com/library/720058/videos"
    assert captured["headers"]["AccessKey"] == "private-upload-api-key"
    assert "private-upload-api-key" not in repr(result)


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_create_video_treats_server_errors_as_uncertain(monkeypatch, status_code):
    _configure_bunny(monkeypatch)

    def fake_request(*args, **kwargs):
        return httpx.Response(status_code, json={"message": "temporary"})

    monkeypatch.setattr("app.services.bunny_stream.httpx.request", fake_request)

    with pytest.raises(BunnyStreamCreateUncertainError):
        create_video("Урок")


def test_create_video_treats_missing_guid_as_uncertain(monkeypatch):
    _configure_bunny(monkeypatch)

    def fake_request(*args, **kwargs):
        return httpx.Response(200, json={"title": "Урок"})

    monkeypatch.setattr("app.services.bunny_stream.httpx.request", fake_request)

    with pytest.raises(BunnyStreamCreateUncertainError):
        create_video("Урок")


def test_delete_video_treats_provider_404_as_idempotent_success(monkeypatch):
    _configure_bunny(monkeypatch)

    class Response:
        status_code = 404
        content = b'{"message":"not found"}'

    monkeypatch.setattr("app.services.bunny_stream.httpx.request", lambda *args, **kwargs: Response())

    assert delete_video(VIDEO_ID) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bunny_stream_library_id", 0),
        ("bunny_stream_video_id", "not-a-guid"),
        ("bunny_stream_token_key", ""),
        ("bunny_stream_token_ttl_seconds", 30),
    ],
)
def test_build_signed_embed_url_rejects_invalid_configuration(monkeypatch, field, value):
    _configure_bunny(monkeypatch)
    monkeypatch.setattr(settings, field, value)

    with pytest.raises(BunnyStreamConfigError):
        build_signed_embed_url()

    assert is_bunny_stream_available() is False
