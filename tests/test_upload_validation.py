from dataclasses import dataclass

import pytest

from app.services.upload_validation import is_allowed_image, read_image_uploads


@dataclass
class FakeUpload:
    filename: str | None
    content_type: str | None
    data: bytes

    async def read(self) -> bytes:
        return self.data


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


@pytest.mark.asyncio
async def test_read_image_uploads_uses_route_specific_format_message():
    files, err = await read_image_uploads(
        [FakeUpload("doc.pdf", "application/pdf", b"pdf")],
        max_files=10,
        unsupported_format_error="Файл «{filename}» — неподдерживаемый формат. Допустимы: JPG, PNG, WebP",
    )

    assert files == []
    assert err == "Файл «doc.pdf» — неподдерживаемый формат. Допустимы: JPG, PNG, WebP"
