"""Тесты загрузки аватара учеником в шапке кабинета.

Эндпоинт: POST /cabinet/avatar
"""
from unittest.mock import patch

_MOCK_S3_UPLOAD = "app.api.cabinet_student.s3_service.upload_to_s3"
_MOCK_S3_CONFIGURED = "app.api.cabinet_student.s3_service.is_configured"

_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16  # minimal JPEG header


def _upload(client, filename="avatar.jpg", content_type="image/jpeg", data=_JPG_BYTES):
    return client.post("/cabinet/avatar", files={"photo": (filename, data, content_type)})


def test_upload_sets_custom_avatar_url(auth_client, db):
    client, user = auth_client
    assert user.custom_avatar_url is None

    with patch(_MOCK_S3_CONFIGURED, return_value=True), \
         patch(_MOCK_S3_UPLOAD, return_value="https://s3.example/avatars/1.jpg"):
        r = _upload(client)

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["avatar_url"] == "https://s3.example/avatars/1.jpg"

    db.refresh(user)
    assert user.custom_avatar_url == "https://s3.example/avatars/1.jpg"


def test_second_upload_replaces_existing(auth_client, db):
    client, user = auth_client
    user.custom_avatar_url = "https://s3.example/avatars/already-set.jpg"
    db.commit()

    with patch(_MOCK_S3_CONFIGURED, return_value=True), \
         patch(_MOCK_S3_UPLOAD, return_value="https://s3.example/avatars/new.jpg"):
        r = _upload(client)

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["avatar_url"] == "https://s3.example/avatars/new.jpg"

    db.refresh(user)
    assert user.custom_avatar_url == "https://s3.example/avatars/new.jpg"


def test_upload_rejects_non_image_format(auth_client, db):
    client, user = auth_client

    r = _upload(client, filename="notes.txt", content_type="text/plain", data=b"hello")

    assert r.status_code == 422
    db.refresh(user)
    assert user.custom_avatar_url is None


def test_upload_without_s3_configured_returns_502(auth_client, db):
    client, user = auth_client

    with patch(_MOCK_S3_CONFIGURED, return_value=False):
        r = _upload(client)

    assert r.status_code == 502
    db.refresh(user)
    assert user.custom_avatar_url is None
