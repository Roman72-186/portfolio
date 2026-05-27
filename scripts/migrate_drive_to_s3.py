"""
Migration: copy portfolio photos from Google Drive to S3.

For each Work record with drive_file_id and no s3_url:
  1. Call n8n webhook portfolio-download-file → get file bytes (base64)
  2. Upload to S3 via app/services/s3.py
  3. Update Work.s3_url and Work.s3_path

Requires N8N_WEBHOOK_DOWNLOAD_FILE to be set in .env
(the URL of the portfolio-download-file n8n workflow).

Usage (on VPS inside the app container):
  docker compose exec app python scripts/migrate_drive_to_s3.py

Or locally (requires .env with all credentials):
  python -m scripts.migrate_drive_to_s3
"""
import asyncio
import base64
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BATCH_PAUSE = 0.5  # seconds between files to not hammer n8n


async def download_from_drive(file_id: str, webhook_url: str) -> bytes | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(webhook_url, json={"file_id": file_id})
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("data", "")
            if not raw:
                logger.warning("Empty data for file_id=%s", file_id)
                return None
            return base64.b64decode(raw)
    except Exception as exc:
        logger.warning("Download failed for file_id=%s: %s", file_id, exc)
        return None


async def main() -> None:
    from app.config import settings
    from app.db.database import SessionLocal
    from app.models.work import Work
    from app.models.user import User
    from app.services.s3 import (
        upload_to_s3, s3_path_before, s3_path_after, is_configured,
    )

    if not settings.n8n_webhook_download_file:
        logger.error(
            "N8N_WEBHOOK_DOWNLOAD_FILE is not set in .env — "
            "create the portfolio-download-file n8n workflow first."
        )
        return

    if not is_configured():
        logger.error("S3 is not configured — check S3_* vars in .env")
        return

    db = SessionLocal()
    try:
        works = (
            db.query(Work)
            .join(User, Work.user_id == User.id)
            .filter(
                Work.drive_file_id.isnot(None),
                Work.s3_url.is_(None),
                Work.work_type.in_(["before", "after"]),
            )
            .add_columns(User.vk_id, User.tariff)
            .all()
        )

        logger.info("Found %d works to migrate Drive→S3", len(works))
        if not works:
            logger.info("Nothing to do.")
            return

        ok = 0
        fail = 0
        for row in works:
            work: Work = row[0]
            vk_id: int = row[1]
            tariff: str = row[2] or "УВЕРЕННЫЙ"
            filename = (work.filename or "photo.jpg").strip()

            image_bytes = await download_from_drive(
                work.drive_file_id, settings.n8n_webhook_download_file
            )
            if not image_bytes:
                fail += 1
                await asyncio.sleep(BATCH_PAUSE)
                continue

            if work.work_type == "before":
                s3_path = s3_path_before(vk_id, tariff, filename)
            else:
                s3_path = s3_path_after(vk_id, tariff, filename)

            s3_url = upload_to_s3(s3_path, image_bytes)
            if not s3_url:
                logger.warning("S3 upload failed for work_id=%s", work.id)
                fail += 1
                await asyncio.sleep(BATCH_PAUSE)
                continue

            work.s3_url = s3_url
            work.s3_path = s3_path
            db.commit()
            logger.info("OK work_id=%s → %s", work.id, s3_url)
            ok += 1
            await asyncio.sleep(BATCH_PAUSE)

        logger.info("Done. Migrated: %d  Failed: %d", ok, fail)
    except Exception as exc:
        db.rollback()
        logger.error("Migration error: %s", exc)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
