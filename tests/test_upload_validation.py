from dataclasses import dataclass, field

import pytest

from app.services.upload_validation import is_allowed_image, read_image_uploads


@dataclass
class FakeUpload:
    filename: str | None
    content_type: str | None
    data: bytes
    requested: list[int] = field(default_factory=list)

    async def read(self, size: int = -1) -> bytes:
        self.requested.append(size)
        return self.data if size < 0 else self.data[:size]


def test_is_allowed_image_accepts_mobile_octet_stream_with_image_extension():
    assert is_allowed_image("application/octet-stream", "photo.heic") is True


def test_is_allowed_image_rejects_explicit_pdf():
    assert is_allowed_image("application/pdf", "scan.jpg") is False


@pytest.mark.asyncio
async def test_read_image_uploads_preserves_filename_and_bytes():
    files, err = await read_image_uploads(
        [FakeUpload("work.jpg", "image/jpeg", b"image-data")],
        max_files=10,
    )

    assert err is None
    assert files == [("work.jpg", b"image-data")]


def test_unsupported_upload_creates_no_work(auth_client, db, monkeypatch):
    _assert_upload_rejected(auth_client, db, monkeypatch, "doc.pdf", "application/pdf", b"PDF")


def _assert_upload_rejected(auth_client, db, monkeypatch, name, mime, content):
    from app.models.work import Work

    client, user = auth_client
    def unexpected_write(*args, **kwargs):
        pytest.fail("Rejected files must not reach storage")

    monkeypatch.setattr("app.api.upload.s3_service.is_configured", lambda: True)
    monkeypatch.setattr("app.api.upload.s3_service.upload_to_s3", unexpected_write)
    monkeypatch.setattr("app.api.upload.send_photo_to_n8n", unexpected_write)
    response = client.post("/upload/api", data={"month": "январь"}, files={"photos": (name, content, mime)})
    assert response.status_code == 422
    assert response.json()["success"] is False
    assert db.query(Work).filter_by(user_id=user.id).count() == 0


@pytest.mark.asyncio
async def test_read_image_uploads_never_reads_past_the_limit():
    import io

    class BoundedUpload:
        filename = "large.jpg"
        content_type = "image/jpeg"

        def __init__(self):
            self.stream = io.BytesIO(b"x" * 50)

        async def read(self, size=-1):
            assert 0 < size <= 11
            chunk = self.stream.read(size)
            assert self.stream.tell() <= 11
            return chunk

    files, error = await read_image_uploads([BoundedUpload()], max_files=1, max_size=10)
    assert error
    assert files == []


def test_oversized_upload_creates_no_work(auth_client, db, monkeypatch):
    from app.api.upload import MAX_SIZE

    _assert_upload_rejected(auth_client, db, monkeypatch, "large.jpg", "image/jpeg", b"x" * (MAX_SIZE + 1))


@pytest.mark.parametrize("content_type, filename", [
    ("image/svg+xml", "work.svg"),
    ("image/svg+xml", "work.jpg"),
    ("application/octet-stream", "work.svg"),
    ("", "work.svg"),
])
def test_is_allowed_image_rejects_svg(content_type, filename):
    """SVG исполняет скрипты, а Pillow его не читает (код-ревью 12.10.2026)."""
    assert is_allowed_image(content_type, filename) is False


def test_prodamus_webhook_ignores_file_parts():
    """Вебхук Продамуса читал файловую часть формы целиком без предела и отдавал
    её в разбор рядом с подписанными полями (код-ревью 12.10.2026). Теперь в
    разбор идут только подписанные текстовые поля, файл не читается."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from app.api import payments as payments_api

    seen = {}
    mini = FastAPI()

    @mini.post("/hook")
    async def hook(request: Request):
        seen.update(await payments_api._signed_fields(request))
        return {}

    with TestClient(mini) as c:
        resp = c.post(
            "/hook",
            data={"order_num": "ap-1", "sum": "100.00"},
            files={"products": ("products.json", b'{"x": 1}' * 100_000, "application/json")},
        )
    assert resp.status_code == 200
    assert seen == {"order_num": "ap-1", "sum": "100.00"}
