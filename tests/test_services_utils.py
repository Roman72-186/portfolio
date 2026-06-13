"""Тесты app/services/utils.py.

Регрессия на ориентацию: вертикальные фото с телефона хранятся как landscape-
пиксели + EXIF Orientation. compress_image пересохраняет JPEG и сбрасывает тег,
поэтому обязан физически повернуть пиксели — иначе вертикальное фото показывается
«лёжа» (горизонтально). См. жалобу пользователя 2026-06-13.
"""
import io

import pytest

from app.services.utils import compress_image, rotate_image_bytes

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _make_jpeg(width: int, height: int, orientation: int | None) -> bytes:
    img = Image.new("RGB", (width, height), "red")
    if orientation is not None:
        exif = img.getexif()
        exif[0x0112] = orientation  # 0x0112 = Orientation
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
    else:
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
    return buf.getvalue()


def test_compress_image_applies_exif_rotation():
    # Landscape pixels (200x100) tagged Orientation=6 = a portrait phone photo.
    data = _make_jpeg(200, 100, orientation=6)
    out = compress_image(data)
    res = Image.open(io.BytesIO(out))

    # Pixels must be physically rotated to portrait...
    assert res.size == (100, 200)
    # ...and the now-redundant orientation tag stripped (so the browser doesn't
    # rotate a second time).
    assert res.getexif().get(0x0112) is None


def test_compress_image_no_exif_leaves_dimensions():
    # A normal landscape photo without orientation tag must stay landscape.
    data = _make_jpeg(200, 100, orientation=None)
    out = compress_image(data)
    res = Image.open(io.BytesIO(out))
    assert res.size == (200, 100)


def test_rotate_image_bytes_swaps_dimensions():
    # 200x100 landscape → after a 90° rotation must become 100x200 portrait,
    # in both directions (superadmin rotate-photo tool).
    data = _make_jpeg(200, 100, orientation=None)
    for clockwise in (True, False):
        out = rotate_image_bytes(data, clockwise=clockwise)
        res = Image.open(io.BytesIO(out))
        assert res.size == (100, 200)


def test_rotate_image_bytes_full_turn_restores_dimensions():
    # Four quarter-turns return to the original orientation (no compounding crop).
    data = _make_jpeg(200, 100, orientation=None)
    cur = data
    for _ in range(4):
        cur = rotate_image_bytes(cur, clockwise=True)
    assert Image.open(io.BytesIO(cur)).size == (200, 100)


def test_rotate_image_bytes_rejects_non_image():
    # A non-image payload (e.g. a video) must raise so the route returns a clean error.
    with pytest.raises(Exception):
        rotate_image_bytes(b"not an image at all", clockwise=True)
