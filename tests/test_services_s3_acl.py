"""ACL-переключатель S3: TimeWeb требует Object ACL, Selectel его отвергает.

Прод переехал на Selectel (S3_USE_ACL=false), TimeWeb отключён 2026-08 —
тумблер остаётся в коде и в тестах на случай отката, обе ветки зафиксированы:
что именно уходит в boto3 при True и при False.
"""
from unittest.mock import MagicMock

import pytest

import app.services.s3 as s3_module
from app.config import settings


@pytest.fixture
def s3_client(monkeypatch):
    """Настроенный S3 с замоканным boto3-клиентом."""
    monkeypatch.setattr(settings, "s3_endpoint", "https://s3.example.test")
    monkeypatch.setattr(settings, "s3_bucket", "test-bucket")
    monkeypatch.setattr(settings, "s3_access_key", "key")
    monkeypatch.setattr(settings, "s3_secret_key", "secret")
    # Иначе на Selectel-окружении (S3_PUBLIC_BASE_URL задан в .env) публичный URL
    # строился бы из боевого домена, а не из mock-эндпоинта выше.
    monkeypatch.setattr(settings, "s3_public_base_url", "")

    client = MagicMock()
    monkeypatch.setattr(s3_module, "_get_client", lambda: client)
    return client


def test_acl_kwargs_enabled_sends_public_read(monkeypatch):
    """Включённый тумблер (боевой режим TimeWeb, пока бакет не удалён) шлёт ACL."""
    monkeypatch.setattr(settings, "s3_use_acl", True)
    assert s3_module._acl_kwargs() == {"ACL": "public-read"}


def test_acl_kwargs_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "s3_use_acl", False)
    assert s3_module._acl_kwargs() == {}


def test_upload_sends_acl_when_enabled(s3_client, monkeypatch):
    monkeypatch.setattr(settings, "s3_use_acl", True)

    url = s3_module.upload_to_s3("path/photo.jpg", b"data")

    assert url == "https://s3.example.test/test-bucket/path/photo.jpg"
    assert s3_client.put_object.call_args.kwargs["ACL"] == "public-read"


def test_upload_omits_acl_when_disabled(s3_client, monkeypatch):
    monkeypatch.setattr(settings, "s3_use_acl", False)

    url = s3_module.upload_to_s3("path/photo.jpg", b"data")

    assert url == "https://s3.example.test/test-bucket/path/photo.jpg"
    assert "ACL" not in s3_client.put_object.call_args.kwargs


def test_move_omits_acl_when_disabled(s3_client, monkeypatch):
    """copy_object — вторая точка, где ACL уходил в запрос."""
    monkeypatch.setattr(settings, "s3_use_acl", False)

    assert s3_module.move_s3_object("old.jpg", "new.jpg") is True
    assert "ACL" not in s3_client.copy_object.call_args.kwargs
    s3_client.delete_object.assert_called_once()


# ---------------------------------------------------------------------------
# Тип объекта в публичном бакете (код-ревью 12.10.2026, P1 № 2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content_type, stored", [
    ("image/jpeg", "image/jpeg"),
    ("video/mp4", "video/mp4"),
    ("video/quicktime", "video/quicktime"),
    ("audio/webm;codecs=opus", "audio/webm"),
    ("Audio/MP4", "audio/mp4"),
])
def test_upload_keeps_media_content_type(s3_client, content_type, stored):
    s3_module.upload_to_s3("path/file.bin", b"data", content_type)

    kwargs = s3_client.put_object.call_args.kwargs
    assert kwargs["ContentType"] == stored
    assert "ContentDisposition" not in kwargs


@pytest.mark.parametrize("content_type", [
    "text/html",
    "image/svg+xml",
    "application/xhtml+xml",
    "application/javascript",
    "",
    None,
    "video/mp4\r\nX-Evil: 1",
])
def test_upload_turns_foreign_content_type_into_download(s3_client, content_type):
    """Видео и голосовое в диалоге ОС проверяются «тип или расширение»:
    `clip.mp4` с типом `text/html` ложился в бакет веб-страницей."""
    s3_module.upload_to_s3("path/clip.mp4", b"<script>alert(1)</script>", content_type)

    kwargs = s3_client.put_object.call_args.kwargs
    assert kwargs["ContentType"] == "application/octet-stream"
    assert kwargs["ContentDisposition"] == "attachment"
