import re
from dataclasses import dataclass, field
from pathlib import Path

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


@pytest.mark.asyncio
async def test_read_image_uploads_uses_route_specific_format_message():
    files, err = await read_image_uploads(
        [FakeUpload("doc.pdf", "application/pdf", b"pdf")],
        max_files=10,
        unsupported_format_error="Файл «{filename}» — неподдерживаемый формат. Допустимы: JPG, PNG, WebP",
    )

    assert files == []
    assert err == "Файл «doc.pdf» — неподдерживаемый формат. Допустимы: JPG, PNG, WebP"


@pytest.mark.asyncio
async def test_read_image_uploads_never_reads_past_the_limit():
    """Код-ревью 28.09.2026, P1: файл читался в память целиком и только потом
    сверялся с лимитом — несколько больших загрузок подряд роняли воркер."""
    upload = FakeUpload("huge.jpg", "image/jpeg", b"x" * 50)
    files, err = await read_image_uploads([upload], max_files=1, max_size=10)

    assert upload.requested == [11]
    assert files == []
    assert err == "Файл «huge.jpg» слишком большой (макс. 10 МБ)"


def test_no_upload_is_read_without_a_size_limit():
    """Сторож на весь `app/`: `await <файл>.read()` без аргумента читает
    загрузку целиком, сколько бы её ни прислали. Читать `read(LIMIT + 1)`
    и сверять длину — образец `app/api/feedback.py`. Тело запроса
    (`await request.body()`) сюда не относится."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders = [
        f"{path.relative_to(app_dir.parent)}:{number}: {line.strip()}"
        for path in app_dir.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"await\s+(?!request\b)\w+\.read\(\s*\)", line)
    ]
    assert offenders == []
