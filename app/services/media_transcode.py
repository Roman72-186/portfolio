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

Не справился ffmpeg (нет бинаря, битый файл, таймаут) — возвращается исходный
файл и пишется ошибка в лог: запись преподавателя лучше сохранить как есть, чем
потерять. Функции синхронные и тяжёлые — звать из executor/`asyncio.to_thread`,
не из event loop.
"""
from __future__ import annotations

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
_TELEGRAM_VOICE_ARGS = [
    "-vn", "-c:a", "libopus", "-b:a", "48k", "-ac", "1", "-ar", "48000",
    "-application", "voip",
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
    рисует голосовое сообщение с волной из OGG/Opus. Не справился ffmpeg —
    вернётся исходный m4a: его `sendVoice` тоже принимает загрузкой файла.
    """
    return _transcode(
        filename, data, content_type,
        out_ext="ogg", out_type="audio/ogg", args=_TELEGRAM_VOICE_ARGS, timeout=VOICE_TIMEOUT_SEC,
    )


def playable_note(filename: str, data: bytes, content_type: str) -> tuple[str, bytes, str]:
    """Кружок → `.mp4` (H.264 + AAC). Возвращает (filename, data, content_type)."""
    return _transcode(
        filename, data, content_type,
        out_ext="mp4", out_type="video/mp4", args=_NOTE_ARGS, timeout=NOTE_TIMEOUT_SEC,
    )


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
