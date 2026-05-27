"""
One-time migration: sync Google Drive photos (До/После) for all existing students.

Finds students where:
  - tg_username is set
  - profile_completed = True
  - Have no Work records with work_type in (before, after)

For each such student, calls sync_drive_works() via n8n → Google Drive.

Usage (on VPS inside the app container):
  docker compose exec app python scripts/migrate_sync_drive_works.py

Or from the scripts directory:
  python -m scripts.migrate_sync_drive_works
"""
import asyncio
import logging
import sys
import os

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    from app.db.database import SessionLocal
    from app.models.user import User
    from app.models.role import Role
    from app.services.drive import sync_drive_works

    db = SessionLocal()
    try:
        # All students with tg_username set and profile completed.
        # sync_drive_works is idempotent: skips photos already in DB by drive_file_id,
        # so running on students who already have works is safe — adds only new photos.
        candidates = (
            db.query(User)
            .join(Role, User.role_id == Role.id, isouter=True)
            .filter(
                User.tg_username.isnot(None),
                User.tg_username != "",
                User.profile_completed.is_(True),
            )
            .all()
        )

        logger.info("Found %d students to sync", len(candidates))
        if not candidates:
            logger.info("Nothing to do.")
            return

        ok = 0
        skip = 0
        for user in candidates:
            tariff = user.tariff or "УВЕРЕННЫЙ"
            tg = user.tg_username
            logger.info(
                "Syncing user_id=%s vk_id=%s tg=%s tariff=%s",
                user.id, user.vk_id, tg, tariff,
            )
            try:
                await sync_drive_works(
                    user_id=user.id,
                    vk_id=user.vk_id,
                    tariff=tariff,
                    tg_username=tg,
                )
                ok += 1
            except Exception as exc:
                logger.error("  FAILED for user_id=%s: %s", user.id, exc)
                skip += 1

            # Small pause to avoid hammering n8n
            await asyncio.sleep(1)

        logger.info("Done. Synced: %d  Failed: %d", ok, skip)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
