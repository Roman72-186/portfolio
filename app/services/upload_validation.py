from __future__ import annotations

from typing import Protocol


MAX_UPLOAD_FILE_SIZE = 10 * 1024 * 1024
MAX_UPLOAD_FILES = 10

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".jfif",
    ".png", ".apng",
    ".webp",
    ".heic", ".heif", ".avif",
    ".gif", ".bmp", ".tif", ".tiff",
    ".svg",
}


class ReadableUpload(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self) -> bytes:
        ...


def is_allowed_image(content_type: str | None, filename: str | None) -> bool:
    ct = (content_type or "").lower()
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""

    if ct.startswith("image/"):
        return True
    if ct.startswith(("video/", "audio/", "text/", "application/pdf", "application/zip")):
        return False
    if ext in IMAGE_EXTENSIONS:
        return True
    if ct in ("application/octet-stream", "binary/octet-stream", "") and ext in IMAGE_EXTENSIONS:
        return True
    return False


async def read_image_uploads(
    photos: list[ReadableUpload],
    *,
    max_files: int,
    max_size: int = MAX_UPLOAD_FILE_SIZE,
    empty_error: str = "Выберите хотя бы одно фото",
    too_many_error: str = "Максимум {max_files} фото за раз",
    unsupported_format_error: str = "Файл «{filename}» — неподдерживаемый формат",
    too_large_error: str = "Файл «{filename}» слишком большой (макс. 10 МБ)",
) -> tuple[list[tuple[str, bytes]], str | None]:
    if not photos or (len(photos) == 1 and not photos[0].filename):
        return [], empty_error
    if len(photos) > max_files:
        return [], too_many_error.format(max_files=max_files)

    files_data: list[tuple[str, bytes]] = []
    for photo in photos:
        filename = photo.filename or "photo.jpg"
        if not is_allowed_image(photo.content_type, photo.filename):
            return [], unsupported_format_error.format(filename=photo.filename)
        photo_bytes = await photo.read()
        if len(photo_bytes) > max_size:
            return [], too_large_error.format(filename=photo.filename)
        files_data.append((filename, photo_bytes))
    return files_data, None
