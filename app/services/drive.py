"""
Google Drive service — fetches student photo galleries via n8n webhook.

All Drive access goes through the n8n "Portfolio API: List Photos" workflow
(credential: Google Drive OAuth2, id=65LjlCG2dVC3VVhE).
No service account or credentials.json required.

Folder search: n8n uses `name contains tg_username` across all tariff subfolders
under the root parent folder. Folder format in Drive:
  {tariff_code}_{tg_username}_{vk_id}  e.g. "02_levkovets_kira_814472488"

tg_username — ник в Telegram, который студент указывает в анкете профиля.
"""
import logging
import time
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.constants import MONTHS
from app.services._http import request_with_retry

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes
_FAILURE_CACHE_TTL = 30  # avoid making every page wait while n8n is unavailable
_UI_REQUEST_TIMEOUT = 3.0
_BACKGROUND_REQUEST_TIMEOUT = 20.0

# vk_id → (timestamp, photos_list)
_photos_cache: dict[int, tuple[float, list[dict]]] = {}
_failure_cache_until: dict[int, float] = {}

# (vk_id, file_id) → photo dict. Scoping prevents cross-user thumbnail access.
_file_index: dict[tuple[int, str], dict] = {}

# Shared persistent client — initialised/closed through app lifespan (see main.py).
_client: httpx.AsyncClient | None = None


async def init_client() -> None:
    global _client
    if not settings.n8n_enabled:
        return
    _client = httpx.AsyncClient(timeout=_BACKGROUND_REQUEST_TIMEOUT)


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_BACKGROUND_REQUEST_TIMEOUT)
    return _client


def _photo_created_dt(photo: dict) -> datetime:
    try:
        return datetime.fromisoformat(photo["created_at"].replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _list_photos_url() -> str:
    return f"{settings.n8n_base_url}/webhook/37XGEC36WlvKBTGl/webhook/portfolio-list-photos"


async def list_student_photos(
    vk_id: int,
    tg_username: str,
    *,
    background: bool = False,
    **_kwargs,
) -> list[dict]:
    """
    Fetch all photos for a student via n8n → Google Drive OAuth2.

    Searches Drive for any folder whose name contains tg_username (substring match).
    Returns list of dicts: id, name, thumbnail_url, view_url, created_at, type
    Results are cached for 5 minutes.
    """
    if not settings.n8n_enabled:
        return []

    entry = _photos_cache.get(vk_id)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    if not background and _failure_cache_until.get(vk_id, 0) > time.time():
        return entry[1] if entry else []

    if not tg_username:
        logger.info("list_student_photos: tg_username not set for vk_id=%s, skipping", vk_id)
        return []

    payload = {
        "parent_id": settings.google_drive_parent_id,
        "student_name": tg_username,
        # Stable identifier lets n8n select the exact ..._{vk_id} folder instead
        # of relying only on a potentially ambiguous username substring.
        "vk_id": vk_id,
    }
    headers = {}
    if settings.n8n_webhook_secret:
        headers["X-Webhook-Secret"] = settings.n8n_webhook_secret

    try:
        client = await _get_client()
        timeout = _BACKGROUND_REQUEST_TIMEOUT if background else _UI_REQUEST_TIMEOUT
        resp = await request_with_retry(
            lambda: client.post(
                _list_photos_url(), json=payload, headers=headers, timeout=timeout,
            ),
            label="drive list-photos",
            max_attempts=3 if background else 1,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("list_student_photos n8n call failed (vk_id=%s): %s", vk_id, exc)
        if not background:
            _failure_cache_until[vk_id] = time.time() + _FAILURE_CACHE_TTL
        # Keep already-renderable thumbnails during a transient n8n outage.
        return entry[1] if entry else []

    if isinstance(data, dict):
        response_vk_id = data.get("vk_id") or data.get("owner_vk_id")
        if response_vk_id is not None and str(response_vk_id) != str(vk_id):
            logger.error(
                "list_student_photos owner mismatch: requested vk_id=%s, response=%s",
                vk_id, response_vk_id,
            )
            return entry[1] if entry else []

    raw_photos = data.get("photos", []) if isinstance(data, dict) else []
    photos = []
    for p in raw_photos:
        photo = {
            "id": p.get("id", ""),
            "name": (p.get("name") or "").strip(),
            "thumbnail_url": p.get("thumbnail", ""),
            "view_url": p.get("download", ""),
            "created_at": p.get("created", ""),
            "type": p.get("type", ""),
        }
        photos.append(photo)

    photos.sort(key=_photo_created_dt, reverse=True)
    # Replace this student's index atomically enough for the single-process
    # in-memory cache: removed Drive files must stop resolving immediately.
    for key in [key for key in _file_index if key[0] == vk_id]:
        _file_index.pop(key, None)
    for photo in photos:
        if photo["id"]:
            _file_index[(vk_id, photo["id"])] = photo
    _photos_cache[vk_id] = (time.time(), photos)
    _failure_cache_until.pop(vk_id, None)
    return photos


def get_photo_thumbnail_url(vk_id: int, file_id: str) -> str | None:
    """Return a cached thumbnail only when it belongs to this student."""
    photo = _file_index.get((vk_id, file_id))
    return photo.get("thumbnail_url") if photo else None


def invalidate_cache(vk_id: int) -> None:
    """Drop cached photos for a student (call after upload)."""
    _photos_cache.pop(vk_id, None)
    _failure_cache_until.pop(vk_id, None)
    for key in [key for key in _file_index if key[0] == vk_id]:
        _file_index.pop(key, None)


_TYPE_MAP = {
    "до": "before",
    "после": "after",
    "before": "before",
    "after": "after",
}


async def sync_drive_works(user_id: int, vk_id: int, tariff: str, tg_username: str) -> None:
    """Background task: pull photos from Drive via n8n and create missing Work records.

    Runs after login. Idempotent — skips photos already present in DB by drive_file_id.
    Does nothing if tg_username is empty (student hasn't filled the profile yet).
    """
    if not settings.n8n_enabled or not tg_username:
        return

    photos = await list_student_photos(
        vk_id=vk_id,
        tariff=tariff,
        tg_username=tg_username,
        background=True,
    )
    if not photos:
        return

    from app.db.database import SessionLocal
    from app.models.work import Work

    db = SessionLocal()
    try:
        existing_ids: set[str] = {
            row[0]
            for row in db.query(Work.drive_file_id)
            .filter(Work.user_id == user_id, Work.drive_file_id.isnot(None))
            .all()
        }

        new_works = []
        for photo in photos:
            file_id = photo.get("id", "")
            if not file_id or file_id in existing_ids:
                continue

            # type comes from subfolder name returned by n8n ("до"/"после"/"before"/"after")
            work_type = _TYPE_MAP.get((photo.get("type") or "").lower(), "after")

            # Parse month from filename: "02_1283364156_Октябрь_237731.jpg" → index 2
            name_parts = photo.get("name", "").rsplit(".", 1)[0].split("_")
            month_from_name = name_parts[2] if len(name_parts) >= 3 else ""

            created_str = photo.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                year = dt.year
                month = month_from_name if month_from_name in MONTHS else MONTHS[dt.month - 1]
            except Exception:
                now = datetime.now(timezone.utc)
                year = now.year
                month = month_from_name if month_from_name in MONTHS else MONTHS[now.month - 1]

            new_works.append(Work(
                user_id=user_id,
                work_type=work_type,
                month=month,
                year=year,
                filename=photo.get("name", "photo.jpg"),
                drive_file_id=file_id,
                tariff=tariff,
                status="success",
            ))

        if new_works:
            db.add_all(new_works)
            db.commit()
            logger.info(
                "sync_drive_works: created %d Work records for user_id=%s",
                len(new_works), user_id,
            )
    except Exception as exc:
        db.rollback()
        logger.error("sync_drive_works failed for user_id=%s: %s", user_id, exc)
    finally:
        db.close()
