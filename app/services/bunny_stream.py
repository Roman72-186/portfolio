"""Bunny Stream playback signing and server-side management API helpers."""

import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import UUID

import httpx

from app.config import settings


BUNNY_API_BASE = "https://video.bunnycdn.com"
BUNNY_TUS_ENDPOINT = f"{BUNNY_API_BASE}/tusupload"
BUNNY_TUS_AUTH_TTL_SECONDS = 86_400


class BunnyStreamConfigError(ValueError):
    """Raised when Bunny Stream configuration is incomplete or invalid."""


class BunnyStreamAPIError(RuntimeError):
    """Raised when Bunny Stream rejects or cannot complete a management request."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BunnyStreamCreateUncertainError(BunnyStreamAPIError):
    """The provider may have created a video but its GUID was not received."""


def _normalize_video_id(video_id: str) -> str:
    try:
        return str(UUID((video_id or "").strip()))
    except (ValueError, AttributeError) as exc:
        raise BunnyStreamConfigError("Bunny Stream video ID is invalid") from exc


def _playback_config(
    video_id: str | None = None, library_id: int | None = None
) -> tuple[int, str, str, int]:
    # Библиотека берётся из записи урока, если она известна: у каждого ролика
    # своя `bunny_library_id`, и при смене BUNNY_STREAM_LIBRARY_ID или появлении
    # второй библиотеки старые уроки иначе получали бы валидно подписанные
    # ссылки в чужую библиотеку — плеер показывал бы заглушку, а причина по коду
    # была бы неочевидна.
    library_id = library_id or settings.bunny_stream_library_id
    normalized_video_id = _normalize_video_id(video_id or settings.bunny_stream_video_id)
    token_key = settings.bunny_stream_token_key.strip()
    ttl_seconds = settings.bunny_stream_token_ttl_seconds

    if library_id <= 0:
        raise BunnyStreamConfigError("Bunny Stream library ID is not configured")
    if not token_key:
        raise BunnyStreamConfigError("Bunny Stream token key is not configured")
    if not 60 <= ttl_seconds <= 86_400:
        raise BunnyStreamConfigError("Bunny Stream token TTL must be between 60 and 86400 seconds")
    return library_id, normalized_video_id, token_key, ttl_seconds


def _management_config() -> tuple[int, str]:
    library_id = settings.bunny_stream_library_id
    api_key = settings.bunny_stream_api_key.strip()
    if library_id <= 0:
        raise BunnyStreamConfigError("Bunny Stream library ID is not configured")
    if not api_key:
        raise BunnyStreamConfigError("Bunny Stream API key is not configured")
    return library_id, api_key


def is_bunny_stream_available() -> bool:
    if not settings.bunny_stream_enabled:
        return False
    try:
        _playback_config()
    except BunnyStreamConfigError:
        return False
    return True


def is_bunny_upload_available() -> bool:
    if not settings.bunny_stream_enabled:
        return False
    try:
        _management_config()
    except BunnyStreamConfigError:
        return False
    return True


def build_signed_embed_url(
    video_id: str | None = None,
    now: datetime | None = None,
    library_id: int | None = None,
) -> str:
    """Build a short-lived Bunny iframe URL without exposing the private key."""
    if not settings.bunny_stream_enabled:
        raise BunnyStreamConfigError("Bunny Stream is disabled")
    library_id, normalized_video_id, token_key, ttl_seconds = _playback_config(
        video_id, library_id
    )
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    expires = int(current_time.timestamp()) + ttl_seconds
    token = hashlib.sha256(
        f"{token_key}{normalized_video_id}{expires}".encode("utf-8")
    ).hexdigest()
    query = urlencode(
        {
            "token": token,
            "expires": expires,
            # Открытие видео уже само по себе клик ученика (карточка
            # раскрывается по «Смотреть») — автостарт экономит второй клик по
            # play внутри плеера (владелец 31.08.2026). Живая проверка
            # показала: голый autoplay=true браузер молча душит (звуковое
            # автовоспроизведение в чужом iframe разрешено не всегда, на
            # iPhone Safari — вообще никогда, правило ОС). muted=true снимает
            # это ограничение безусловно — Bunny стартует без звука сразу,
            # кнопка «Включить звук» поверх плеера (video.html/cabinet_video.html)
            # даёт досмотреть уже со звуком одним кликом.
            "autoplay": "true",
            "muted": "true",
            # Keep iPhone playback inside the iframe. Native iOS fullscreen
            # would detach the video from our per-viewer watermark layer.
            #
            # ДОПУЩЕНИЕ, которое некому проверить автоматически: удержание видео
            # в рамке на iPhone держится на этих двух параметрах плеера Bunny.
            # Из cross-origin iframe уход видео в нативный полноэкранный режим
            # клиенту не виден, поэтому переименование или отмена параметра на
            # стороне провайдера снимет ватермарку молча — ни тест, ни логи этого
            # не заметят. Проверять руками на iPhone после смен версии плеера.
            "playsinline": "true",
            "disableIosPlayer": "true",
        }
    )
    return (
        f"https://iframe.mediadelivery.net/embed/{library_id}/"
        f"{normalized_video_id}?{query}"
    )


def build_tus_credentials(video_id: str, *, now: int | None = None) -> dict:
    """Create short-lived browser upload credentials without returning the API key."""
    if not settings.bunny_stream_enabled:
        raise BunnyStreamConfigError("Bunny Stream is disabled")
    library_id, api_key = _management_config()
    normalized_video_id = _normalize_video_id(video_id)
    expires = (now if now is not None else int(time.time())) + BUNNY_TUS_AUTH_TTL_SECONDS
    signature = hashlib.sha256(
        f"{library_id}{api_key}{expires}{normalized_video_id}".encode("utf-8")
    ).hexdigest()
    return {
        "endpoint": BUNNY_TUS_ENDPOINT,
        "library_id": library_id,
        "video_id": normalized_video_id,
        "authorization_expire": expires,
        "authorization_signature": signature,
    }


def _api_request(method: str, path: str, **kwargs) -> dict:
    library_id, api_key = _management_config()
    url = f"{BUNNY_API_BASE}/library/{library_id}{path}"
    headers = {"AccessKey": api_key, "Accept": "application/json"}
    headers.update(kwargs.pop("headers", {}))
    try:
        response = httpx.request(
            method,
            url,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=10.0),
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise BunnyStreamAPIError("Bunny Stream is temporarily unavailable") from exc
    if response.status_code >= 400:
        raise BunnyStreamAPIError(
            f"Bunny Stream request failed ({response.status_code})",
            status_code=response.status_code,
        )
    if not response.content:
        return {}
    try:
        data = response.json()
    except ValueError as exc:
        raise BunnyStreamAPIError("Bunny Stream returned an invalid response") from exc
    if not isinstance(data, dict):
        raise BunnyStreamAPIError("Bunny Stream returned an invalid response")
    return data


def create_video(title: str) -> dict:
    """Create exactly one Bunny video object. Callers must not blindly retry failures."""
    try:
        data = _api_request(
            "POST",
            "/videos",
            headers={"Content-Type": "application/json"},
            json={"title": title},
        )
    except BunnyStreamAPIError as exc:
        if exc.status_code is None or exc.status_code >= 500:
            raise BunnyStreamCreateUncertainError(
                "Bunny Stream create result is unknown"
            ) from exc
        raise
    try:
        _normalize_video_id(str(data.get("guid", "")))
    except BunnyStreamConfigError as exc:
        raise BunnyStreamCreateUncertainError(
            "Bunny Stream create result is unknown"
        ) from exc
    return data


def get_video(video_id: str) -> dict:
    return _api_request("GET", f"/videos/{_normalize_video_id(video_id)}")


def delete_video(video_id: str) -> None:
    try:
        _api_request("DELETE", f"/videos/{_normalize_video_id(video_id)}")
    except BunnyStreamAPIError as exc:
        if exc.status_code == 404:
            return
        raise


# Числовые статусы видео у Bunny (VideoModelStatus в их справочнике API):
# 0 Created, 1 Uploaded, 2 Processing, 3 Transcoding, 4 Finished, 5 Error,
# 6 UploadFailed, 7 JitSegmenting, 8 JitPlaylistsCreated.
_BUNNY_STATUS = {
    0: "uploading",    # объект создан, файл ещё не приехал
    1: "processing",
    2: "processing",
    3: "ready",        # Transcoding: JIT отдаёт видео играбельным уже здесь
    4: "ready",
    5: "failed",
    6: "failed",       # UploadFailed — именно провал, а не идущая загрузка
    7: "processing",
    8: "ready",        # JitPlaylistsCreated — плейлисты собраны, это успех
}


def normalize_bunny_status(status: int | None) -> str:
    """Статус Bunny → наш.

    Прежняя таблица расходилась со справочником в трёх местах, и каждое
    расхождение било по делу: `6` (UploadFailed) показывался как «идёт загрузка»,
    поэтому брошенная или сорвавшаяся загрузка навсегда оставалась строкой,
    которую админка бесконечно опрашивала; `8` (JitPlaylistsCreated) считался
    отказом, хотя это готовое видео; `0` (Created) попадал в «обрабатывается» и
    выглядел как живая работа Bunny, хотя файл к провайдеру ещё не поступал.

    `3` (Transcoding) намеренно оставлен готовым: при JIT-энкодинге видео уже
    воспроизводится, ждать `4` значит держать урок закрытым без нужды.
    Неизвестный код трактуем как «обрабатывается» — это единственное состояние,
    из которого система сама выходит по опросу.
    """
    return _BUNNY_STATUS.get(status, "processing")
