import base64
import logging

import httpx

from app.config import settings
from app.constants import TARIFF_CODES

logger = logging.getLogger(__name__)

# Client is initialised / closed through the app lifespan (see app/main.py).
_client: httpx.AsyncClient | None = None


async def init_client() -> None:
    """Create the shared httpx client. Called once at application startup."""
    global _client
    _client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0), limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))


async def close_client() -> None:
    """Close the shared httpx client. Called once at application shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_client() -> httpx.AsyncClient:
    """Return the shared client, creating it on-demand if lifespan didn't run (e.g. tests)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0), limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
    return _client


async def send_photo_to_n8n(
    user_id: int,
    student_name: str,
    tariff: str,
    month: str,
    photo_bytes: bytes,
    filename: str,
    photo_type: str = "after",
    s3_path: str | None = None,
) -> dict:
    """Send photo to n8n webhook for Google Drive upload.

    photo_type: "before" | "after" | "mock_exam"
    s3_path: already-uploaded S3 path (so n8n can skip S3 and mirror to Drive only)
    """
    photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")

    payload = {
        "vk_id": user_id,
        "student_name": student_name,
        "tariff": tariff,
        "tariff_code": TARIFF_CODES.get(tariff.upper(), "02"),
        "month": month,
        "filename": filename,
        "photo_base64": photo_b64,
        "photo_type": photo_type,
        "s3_path": s3_path,
        "source": "web_cabinet",
    }

    try:
        client = await _get_client()
        resp = await client.post(settings.n8n_webhook_upload, json=payload)
        resp.raise_for_status()
        result = resp.json()
        logger.info("n8n upload OK: %s", result)
        return result
    except httpx.TimeoutException:
        logger.error("n8n upload timeout after 90s")
        return {"success": False, "error": "Таймаут загрузки. Попробуйте снова."}
    except httpx.HTTPStatusError as e:
        logger.error("n8n upload HTTP error: %s %s", e.response.status_code, e.response.text[:200])
        return {"success": False, "error": f"Ошибка сервера: {e.response.status_code}"}
    except Exception as e:
        logger.error("n8n upload failed: %s", e)
        return {"success": False, "error": str(e)}
