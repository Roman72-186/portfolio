"""Голосовое и кружок перегоняются в форматы, которые играет любой телефон.

Прод-инцидент 29.09.2026: голосовое преподавателя из Chrome (WebM/Opus без
длины и оглавления) у ученика на телефоне играло секунд пять и обрывалось.
Теперь всё, что ученик слушает как запись преподавателя, до S3 проходит через
`app/services/media_transcode.py`: голос → AAC `.m4a`, кружок → H.264 `.mp4`.

Тесты с настоящим ffmpeg пропускаются, если его нет на машине; остальные
подменяют перекодирование и проверяют, что его зовёт каждый вход.
"""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.task_block import MEDIA_NOTE, MEDIA_VOICE
from app.services import (
    feedback as feedback_service,
    homework_feedback as homework_feedback_service,
    media_transcode,
    s3 as s3_service,
    task_block_feedback as task_block_feedback_service,
)

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="нет ffmpeg/ffprobe",
)

PROGRAM = "/cabinet/staff/program"
URL = "https://s3.example.com/record"


def _lavfi(tmp_path: Path, name: str, args: list[str]) -> bytes:
    """Сгенерировать запись в формате, который пишет MediaRecorder браузера."""
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args, str(out)], check=True,
    )
    return out.read_bytes()


def _probe(tmp_path: Path, name: str, data: bytes) -> dict:
    path = tmp_path / name
    path.write_bytes(data)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True,
    )
    return json.loads(out.stdout)


# ── Настоящий ffmpeg ───────────────────────────────────────────────────────

@needs_ffmpeg
def test_voice_from_browser_webm_becomes_m4a_with_duration(tmp_path):
    webm = _lavfi(tmp_path, "src.webm", [
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "libopus",
    ])

    name, data, mime = media_transcode.playable_voice("voice-1.webm", webm, "audio/webm")

    assert (name, mime) == ("voice-1.m4a", "audio/mp4")
    info = _probe(tmp_path, "out.m4a", data)
    assert [s["codec_name"] for s in info["streams"]] == ["aac"]
    # Длина прописана в файле — именно её не было у браузерного webm.
    assert float(info["format"]["duration"]) == pytest.approx(3, abs=0.2)


@needs_ffmpeg
def test_note_from_browser_webm_becomes_h264_mp4_with_even_sides(tmp_path):
    # Нечётные стороны — libx264 их не принимает, фильтр обязан выровнять.
    webm = _lavfi(tmp_path, "src.webm", [
        "-f", "lavfi", "-i", "testsrc=size=321x241:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libvpx", "-c:a", "libopus", "-shortest",
    ])

    name, data, mime = media_transcode.playable_note("circle-1.webm", webm, "video/webm")

    assert (name, mime) == ("circle-1.mp4", "video/mp4")
    info = _probe(tmp_path, "out.mp4", data)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert video["width"] % 2 == 0 and video["height"] % 2 == 0
    assert audio["codec_name"] == "aac"


@needs_ffmpeg
def test_broken_file_is_kept_as_is(caplog):
    """Битый файл не теряется: запись преподавателя важнее формата."""
    result = media_transcode.playable_voice("voice.webm", b"not a media file", "audio/webm")

    assert result == ("voice.webm", b"not a media file", "audio/webm")
    assert "media transcode failed" in caplog.text


def test_without_ffmpeg_file_is_kept_as_is(caplog):
    with patch.object(media_transcode.shutil, "which", return_value=None):
        result = media_transcode.playable_note("circle.webm", b"bytes", "video/webm")

    assert result == ("circle.webm", b"bytes", "video/webm")
    assert "ffmpeg not found" in caplog.text


def test_timeout_keeps_file_as_is(caplog):
    with (
        patch.object(media_transcode.shutil, "which", return_value="ffmpeg"),
        patch.object(
            media_transcode.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1),
        ),
    ):
        result = media_transcode.playable_voice("voice.webm", b"bytes", "audio/webm")

    assert result == ("voice.webm", b"bytes", "audio/webm")
    assert "timeout" in caplog.text


# ── Каждый вход зовёт перекодирование ──────────────────────────────────────

def _fake_voice(filename, data, content_type):
    return "voice-1.m4a", b"aac-bytes", "audio/mp4"


def _fake_note(filename, data, content_type):
    return "circle-1.mp4", b"h264-bytes", "video/mp4"


def test_upload_media_stores_transcoded_voice_and_note(client, user_factory, session_factory):
    user = user_factory(
        vk_id=982_001, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    client.cookies.set("session_id", session_factory(user).id)

    with (
        patch.object(media_transcode, "playable_voice", side_effect=_fake_voice) as voice,
        patch.object(media_transcode, "playable_note", side_effect=_fake_note) as note,
        patch.object(s3_service, "upload_to_s3", return_value=URL) as upload,
    ):
        voice_resp = client.post(
            f"{PROGRAM}/upload-media", data={"kind": MEDIA_VOICE},
            files={"file": ("voice-1.webm", b"webm-bytes", "audio/webm;codecs=opus")},
        )
        voice_upload = upload.call_args.args
        note_resp = client.post(
            f"{PROGRAM}/upload-media", data={"kind": MEDIA_NOTE},
            files={"file": ("circle-1.webm", b"webm-bytes", "video/webm")},
        )
        note_upload = upload.call_args.args

    assert voice_resp.status_code == 200, voice_resp.text
    assert voice.call_args.args == ("voice-1.webm", b"webm-bytes", "audio/webm")
    assert voice_resp.json()["path"].endswith(".m4a")
    assert voice_upload[1:] == (b"aac-bytes", "audio/mp4")

    assert note_resp.status_code == 200, note_resp.text
    assert note.call_args.args == ("circle-1.webm", b"webm-bytes", "video/webm")
    assert note_resp.json()["path"].endswith(".mp4")
    assert note_upload[1:] == (b"h264-bytes", "video/mp4")


CHAT_SERVICES = [
    pytest.param(feedback_service, id="работа"),
    pytest.param(homework_feedback_service, id="домашка"),
    pytest.param(task_block_feedback_service, id="блок-задания"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("service", CHAT_SERVICES)
async def test_chat_voice_is_transcoded(service):
    with (
        patch.object(media_transcode, "playable_voice", side_effect=_fake_voice),
        patch.object(s3_service, "upload_to_s3", return_value=URL) as upload,
    ):
        path, url = await service._upload_audio(7, "voice-1.webm", b"webm", "audio/webm")

    assert url == URL
    assert path.endswith(".m4a")
    assert upload.call_args.args == (path, b"aac-bytes", "audio/mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize("service", CHAT_SERVICES)
async def test_chat_note_is_transcoded_but_regular_video_is_not(service):
    """Обычное видео в переписке бывает до 500 МБ — его не трогаем, только кружок."""
    with (
        patch.object(media_transcode, "playable_note", side_effect=_fake_note) as note,
        patch.object(s3_service, "upload_to_s3", return_value=URL) as upload,
    ):
        path, _ = await service._upload_video(7, "clip.mov", b"mov", "video/quicktime")
        assert note.call_count == 0
        assert path.endswith(".mov")
        assert upload.call_args.args[1:] == (b"mov", "video/quicktime")

        path, _ = await service._upload_video(7, "circle-1.webm", b"webm", "video/webm", note=True)

    assert note.call_count == 1
    assert path.endswith(".mp4")
    assert upload.call_args.args == (path, b"h264-bytes", "video/mp4")
