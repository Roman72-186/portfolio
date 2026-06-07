"""TimeWeb S3 storage service.

Upload photos to S3. Returns the public URL of the uploaded object.
Falls back gracefully (returns None) when S3 credentials are not configured.

Path conventions:
  BEFORE:    Портфолио/{тариф}/{тариф}_{vk_id}/До/{тариф}_{vk_id}_{random8}.ext
  AFTER:     Портфолио/{тариф}/{тариф}_{vk_id}/После/{тариф}_{vk_id}_{random8}.ext
  MOCK EXAM: Пробники/{тариф}/{тариф}_{vk_id}/{YYYY-MM}/{тариф}_{vk_id}_{random8}.ext
  RETAKE:    Отработки/{тариф}/{тариф}_{vk_id}/{YYYY-MM}/{тариф}_{vk_id}_{random8}.ext
"""
import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from app.config import settings
from app.constants import TARIFF_DISPLAY

logger = logging.getLogger(__name__)


def tariff_display(tariff: str) -> str:
    """Return display form of tariff for use in S3 paths."""
    return TARIFF_DISPLAY.get(tariff.upper(), tariff)


def _make_filename(tariff: str, vk_id: int, original: str) -> str:
    """Generate new filename: {тариф}_{vk_id}_{random8}.ext"""
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else "jpg"
    rnd = uuid.uuid4().hex[:8]
    return f"{tariff_display(tariff)}_{vk_id}_{rnd}.{ext}"


def is_configured() -> bool:
    return bool(settings.s3_endpoint and settings.s3_bucket and settings.s3_access_key)


@lru_cache(maxsize=1)
def _get_client():
    """Build and cache a boto3 S3 client."""
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def s3_path_before(vk_id: int, tariff: str, filename: str) -> str:
    tf = tariff_display(tariff)
    return f"Портфолио/{tf}/{tf}_{vk_id}/До/{_make_filename(tariff, vk_id, filename)}"


def s3_path_after(vk_id: int, tariff: str, filename: str) -> str:
    tf = tariff_display(tariff)
    return f"Портфолио/{tf}/{tf}_{vk_id}/После/{_make_filename(tariff, vk_id, filename)}"


def s3_path_mock_exam(vk_id: int, tariff: str, filename: str) -> str:
    tf = tariff_display(tariff)
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    return f"Пробники/{tf}/{tf}_{vk_id}/{ym}/{_make_filename(tariff, vk_id, filename)}"


def s3_path_retake(vk_id: int, tariff: str, filename: str) -> str:
    tf = tariff_display(tariff)
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    return f"Отработки/{tf}/{tf}_{vk_id}/{ym}/{_make_filename(tariff, vk_id, filename)}"


def s3_path_probnik_cycle(vk_id: int, cycle_id: int, attempt: int, kind: str, filename: str, tariff: str = "") -> str:
    """Цикл Пробника: probniki/{vk_id}/{cycle_id}/attempt-{n}/final|intermediate/{filename}."""
    assert kind in ("final", "intermediate")
    return f"probniki/{vk_id}/{cycle_id}/attempt-{attempt}/{kind}/{_make_filename(tariff or 'X', vk_id, filename)}"


def s3_path_otrabotka_cycle(vk_id: int, cycle_id: int, attempt: int, kind: str, filename: str, tariff: str = "") -> str:
    """Цикл Отработки: otrabotki/{vk_id}/{cycle_id}/attempt-{n}/final|intermediate/{filename}."""
    assert kind in ("final", "intermediate")
    return f"otrabotki/{vk_id}/{cycle_id}/attempt-{attempt}/{kind}/{_make_filename(tariff or 'X', vk_id, filename)}"


def s3_path_feedback(work_id: int, filename: str) -> str:
    """Файлы обратной связи: feedback/{work_id}/{filename}."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    rnd = uuid.uuid4().hex[:8]
    return f"feedback/{work_id}/{rnd}.{ext}"


def s3_path_curator_report(curator_id: int, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4"
    rnd = uuid.uuid4().hex[:12]
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    return f"curator-reports/{curator_id}/{ym}/{rnd}.{ext}"


def s3_public_url(s3_path: str) -> str:
    """Construct the public URL for an S3 object."""
    endpoint = settings.s3_endpoint.rstrip("/")
    return f"{endpoint}/{settings.s3_bucket}/{s3_path}"


def s3_path_from_public_url(url: str) -> str | None:
    prefix = s3_public_url("")
    if url.startswith(prefix):
        return url[len(prefix):]
    return None


def move_s3_object(old_path: str, new_path: str) -> bool:
    """Copy object to new S3 key, then delete the old key. Returns True on success."""
    if not is_configured():
        return False
    try:
        client = _get_client()
        client.copy_object(
            Bucket=settings.s3_bucket,
            CopySource={"Bucket": settings.s3_bucket, "Key": old_path},
            Key=new_path,
            ACL="public-read",
        )
        client.delete_object(Bucket=settings.s3_bucket, Key=old_path)
        return True
    except Exception as exc:
        logger.error("S3 move failed %s -> %s: %s", old_path, new_path, exc)
        return False


def delete_from_s3(s3_path: str) -> bool:
    """Delete an object from S3. Returns True on success."""
    if not is_configured():
        return False
    try:
        client = _get_client()
        client.delete_object(Bucket=settings.s3_bucket, Key=s3_path)
        return True
    except Exception as exc:
        logger.error("S3 delete failed %s: %s", s3_path, exc)
        return False


def upload_to_s3(s3_path: str, data: bytes, content_type: str = "image/jpeg") -> str | None:
    """Upload bytes to S3. Returns public URL or None on failure / unconfigured."""
    if not is_configured():
        logger.debug("S3 not configured — skipping upload")
        return None
    try:
        client = _get_client()
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=s3_path,
            Body=data,
            ContentType=content_type,
            ACL="public-read",
        )
        url = s3_public_url(s3_path)
        logger.info("S3 upload OK: %s", url)
        return url
    except Exception as exc:
        logger.error("S3 upload failed for %s: %s", s3_path, exc)
        return None
