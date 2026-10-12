"""Голосовое и кружок — в форматы, которые играет любой телефон.

Браузер пишет запись через MediaRecorder в WebM/Opus (Chrome, Firefox и свежий
Safari), причём «как трансляцию»: у файла нет длины и оглавления (`Duration`,
`Cues`), сегмент и кластеры неизвестного размера. iPhone старше iOS 17.4 такой
звук не играет вовсе, новее — рассчитан на конечный файл. Прод-инцидент
29.09.2026: ученик слушал голосовое преподавателя в задании секунд пять, дальше
оно не грузилось, хотя внутри файла были целые 3 минуты (`docs/ops-history.md`).

Поэтому всё, что ученик слушает или смотрит как запись преподавателя, до S3
перегоняется: голосовое → AAC в `.m4a`, кружок → H.264 + AAC в `.mp4`, оба с
оглавлением в начале файла (`+faststart`). Перекодируется любой вход, не только
webm: одно правило вместо списка «хороших» форматов, для голоса это доли секунды.

Фото рассылки для Telegram (`telegram_photo`) тоже здесь, хотя идёт через
Pillow: модуль — владелец правила «в каком виде вложение уходит в Telegram».

Не справился ffmpeg (нет бинаря, битый файл, таймаут) — возвращается исходный
файл и пишется ошибка в лог: запись преподавателя лучше сохранить как есть, чем
потерять. Функции синхронные и тяжёлые — звать из executor/`asyncio.to_thread`,
не из event loop.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

VOICE_TIMEOUT_SEC = 120
NOTE_TIMEOUT_SEC = 300

_VOICE_ARGS = [
    "-vn", "-c:a", "aac", "-b:a", "96k", "-ac", "1",
    "-movflags", "+faststart",
]
# Голосовое сообщение Telegram — OGG с кодеком Opus, родной формат его
# голосовых. Кодек задаётся явно: для `.ogg` ffmpeg по умолчанию берёт Vorbis,
# а такой файл Telegram показывает документом, а не голосовым.
#
# Качество — как у исходника (жалоба на рассылки 12.10.2026: «голосовые плохого
# качества»). Запись браузера и голосовые из Telegram уже Opus: такой звук
# только перекладывается в OGG без перекодирования (`_TELEGRAM_VOICE_COPY_ARGS`).
# Остальное кодируется с запасом, каналы не сводятся: прикреплённая музыка
# остаётся стерео. Раньше было 48 кбит/с моно в режиме «речь» поверх 64 кбит/с
# записи — вторая потеря на каждом голосовом.
_TELEGRAM_VOICE_COPY_ARGS = ["-vn", "-c:a", "copy"]
_TELEGRAM_VOICE_ARGS = [
    "-vn", "-c:a", "libopus", "-b:a", "128k", "-ar", "48000",
]
# Кружок у ученика — круг до 360 px (`.lrn-blk-note`), 640 по ширине с запасом.
# Чётные стороны и yuv420p обязательны для libx264, иначе он падает, и молча
# уехал бы исходный webm. Два потока — чтобы сервер на 4 ядрах не встал колом
# на время перекодирования.
_NOTE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
    "-vf", "scale='trunc(min(640,iw)/2)*2':-2",
    "-c:a", "aac", "-b:a", "96k", "-ac", "1",
    "-movflags", "+faststart", "-threads", "2",
]
# Кружок Telegram (`sendVideoNote`, рассылки — `services/broadcast_delivery.py`):
# только квадрат, сторона до 640 и не длиннее минуты, иначе Telegram отказывает.
# Браузер пишет кружок обычным прямоугольником (круг — маска на экране), поэтому
# центр вырезается квадратом. Кавычки в фильтре держат запятую внутри `min`.
#
# Кружок рассылки уходит каждому ученику и хранится один раз, поэтому
# кодируется на качество, а не на скорость (жалоба 12.10.2026: «кружочки
# плохого качества» — было crf 28 и veryfast): crf 20, preset medium,
# уменьшение lanczos. Потолок битрейта держит минуту кружка в пределах ~30 МБ:
# Telegram принимает от бота файл до 50 МБ, а грузится он через прокси.
TELEGRAM_NOTE_MAX_SECONDS = 60
_TELEGRAM_NOTE_ARGS = [
    "-t", str(TELEGRAM_NOTE_MAX_SECONDS),
    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
    "-maxrate", "4M", "-bufsize", "8M", "-pix_fmt", "yuv420p",
    "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale='trunc(min(640,iw)/2)*2':'trunc(min(640,iw)/2)*2':flags=lanczos,setsar=1",
    "-c:a", "aac", "-b:a", "128k",
    "-movflags", "+faststart", "-threads", "2",
]

# Файл присылает пользователь, а ffmpeg умеет форматы-ссылки: плейлист HLS,
# манифест DASH, concat-скрипт открывают другие файлы и URL (проверено
# 29.09.2026: `.m3u8` со строкой-путём читал файл с диска сервера). Поэтому
# вход — только контейнеры из списков `ALLOWED_FEEDBACK_*` в
# `services/feedback.py` (имена — из `ffmpeg -demuxers` образа), а вложенные
# открытия — только локальные файлы. Новый допустимый формат загрузки —
# дописать его демультиплексор сюда, иначе он молча уйдёт «как есть».
_INPUT_GUARD = [
    "-format_whitelist", "matroska,mov,mp3,ogg,wav,aac,amr,amrnb,amrwb,avi,asf",
    "-protocol_whitelist", "file",
]


def playable_voice(filename: str, data: bytes, content_type: str) -> tuple[str, bytes, str]:
    """Голосовое → `.m4a` (AAC). Возвращает (filename, data, content_type)."""
    return _transcode(
        filename, data, content_type,
        out_ext="m4a", out_type="audio/mp4", args=_VOICE_ARGS, timeout=VOICE_TIMEOUT_SEC,
    )


def telegram_voice(filename: str, data: bytes, content_type: str) -> tuple[str, bytes, str]:
    """Голосовое → `.ogg` (Opus) для `sendVoice` в Telegram.

    В S3 голосовое лежит m4a (его играет `<audio>` на сайте), а Telegram
    рисует голосовое сообщение с волной из OGG/Opus. Звук в Opus только
    перекладывается в OGG, без потерь; не вышло — кодируется. Не справился
    ffmpeg — вернётся исходный файл: `sendVoice` принимает и m4a, и mp3.
    """
    if _audio_codec(data) == "opus":
        copied = _transcode(
            filename, data, content_type,
            out_ext="ogg", out_type="audio/ogg", args=_TELEGRAM_VOICE_COPY_ARGS,
            timeout=VOICE_TIMEOUT_SEC,
        )
        if copied[1] is not data:
            return copied
    return _transcode(
        filename, data, content_type,
        out_ext="ogg", out_type="audio/ogg", args=_TELEGRAM_VOICE_ARGS, timeout=VOICE_TIMEOUT_SEC,
    )


def _audio_codec(data: bytes) -> str | None:
    """Кодек первой звуковой дорожки (`opus`, `aac`…) или None, если не понять.

    Ошибка здесь не страшна: без ответа голосовое просто перекодируется.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    with tempfile.TemporaryDirectory(prefix="media-") as tmp:
        src = Path(tmp) / "input"
        src.write_bytes(data)
        try:
            result = subprocess.run(
                [
                    ffprobe, "-v", "error", *_INPUT_GUARD,
                    "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(src),
                ],
                capture_output=True, timeout=VOICE_TIMEOUT_SEC,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip() or None


def playable_note(filename: str, data: bytes, content_type: str) -> tuple[str, bytes, str]:
    """Кружок → `.mp4` (H.264 + AAC). Возвращает (filename, data, content_type)."""
    return _transcode(
        filename, data, content_type,
        out_ext="mp4", out_type="video/mp4", args=_NOTE_ARGS, timeout=NOTE_TIMEOUT_SEC,
    )



def telegram_note(filename: str, data: bytes, content_type: str) -> tuple[str, bytes, str]:
    """Кружок для Telegram → квадратный `.mp4` не длиннее минуты.

    На сайте его же играет обычный `<video>` (`.msg-video-note` рисует круг),
    поэтому отдельной копии «для сайта» не нужно. Не справился ffmpeg —
    возвращается исходный файл, как у остальных функций модуля: Telegram такой
    кружок, скорее всего, не примет, и это покажет проверка у преподавателя.
    """
    return _transcode(
        filename, data, content_type,
        out_ext="mp4", out_type="video/mp4", args=_TELEGRAM_NOTE_ARGS, timeout=NOTE_TIMEOUT_SEC,
    )


# Фото для `sendPhoto`. Telegram сам ужимает любое фото до своих размеров (самое
# крупное — 2560 по длинной стороне), поэтому больше 2560 слать незачем, а
# меньше — значит потерять качество до него (жалоба 12.10.2026: было 1600 px и
# JPEG 82, и Telegram сжимал уже сжатое). Пределы Bot API: файл до 10 МБ,
# ширина плюс высота — до 10000.
TELEGRAM_PHOTO_MAX_SIDE = 2560
TELEGRAM_PHOTO_MAX_BYTES = 10 * 1024 * 1024
_PHOTO_QUALITIES = (95, 90, 85)


def telegram_photo(data: bytes) -> tuple[str, bytes, str]:
    """Фото рассылки → JPEG почти без потерь. Возвращает (filename, data, content_type).

    Пиксели не уменьшаются, если длинная сторона до 2560; JPEG 95 без
    прореживания цвета (4:4:4) на глаз от исходника не отличить. Пересохранение
    всё равно нужно: HEIC с iPhone не открывают ни Telegram, ни браузер, поворот
    из EXIF надо применить к пикселям, а сами метаданные (в том числе место
    съёмки) — не отдавать ученикам вместе с файлом из хранилища.

    Не картинка — ValueError с текстом для человека.
    """
    from PIL import Image, ImageFile, ImageOps

    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    try:
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as exc:
        logger.warning("broadcast photo unreadable: %s", exc)
        raise ValueError("Не получилось открыть фото — попробуйте другой файл") from exc

    if img.mode in ("RGBA", "LA", "P"):
        # Прозрачность у JPEG нет: без белой подложки прозрачное стало бы чёрным.
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, (255, 255, 255))
        img.paste(rgba, mask=rgba.getchannel("A"))
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if max(img.size) > TELEGRAM_PHOTO_MAX_SIDE:
        img.thumbnail((TELEGRAM_PHOTO_MAX_SIDE, TELEGRAM_PHOTO_MAX_SIDE), Image.LANCZOS)

    out = b""
    while True:
        for quality in _PHOTO_QUALITIES:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, subsampling=0, optimize=True)
            out = buf.getvalue()
            if len(out) <= TELEGRAM_PHOTO_MAX_BYTES:
                logger.info(
                    "broadcast photo: %dx%d, quality %d, %d -> %d bytes",
                    img.width, img.height, quality, len(data), len(out),
                )
                return "photo.jpg", out, "image/jpeg"
        # Шум на 2560 px может не влезть в 10 МБ даже на 85 — тогда меньше пикселей.
        img = img.resize((img.width * 3 // 4, img.height * 3 // 4), Image.LANCZOS)


def _transcode(
    filename: str, data: bytes, content_type: str,
    *, out_ext: str, out_type: str, args: list[str], timeout: int,
) -> tuple[str, bytes, str]:
    original = (filename, data, content_type)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("media transcode skipped: ffmpeg not found, %s stored as is", filename)
        return original

    # Вход и выход — файлы, не pipe: у m4a/mov с телефона оглавление бывает в
    # конце, из потока такой файл не разобрать, а `+faststart` нужен
    # перематываемый выход. Имя входа нейтральное, без расширения из
    # загрузки: по расширению ffmpeg выбирает формат (`.m3u8` → HLS), формат
    # определяется по содержимому в пределах `_INPUT_GUARD`.
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="media-") as tmp:
        src = Path(tmp) / "input"
        dst = Path(tmp) / f"out.{out_ext}"
        src.write_bytes(data)
        cmd = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            *_INPUT_GUARD, "-i", str(src), "-map_metadata", "-1", *args, str(dst),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.error("media transcode timeout after %ss for %s, stored as is", timeout, filename)
            return original
        except OSError as exc:
            logger.error("media transcode failed to start for %s: %s", filename, exc)
            return original
        if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
            logger.error(
                "media transcode failed for %s (code %s), stored as is: %s",
                filename, result.returncode, stderr,
            )
            return original
        out = dst.read_bytes()

    logger.info(
        "media transcode OK: %s %s -> %s, %d -> %d bytes, %.1fs",
        filename, content_type, out_type, len(data), len(out), time.monotonic() - started,
    )
    return f"{Path(filename).stem or 'record'}.{out_ext}", out, out_type
