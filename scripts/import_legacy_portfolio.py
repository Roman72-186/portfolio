"""Import historical student photos from an external JSON archive.

The payload must stay outside the repository and have this shape:

[
  {
    "user_id": 123,
    "dialog_id": "456",
    "photos": [
      {"filename": "photo.jpg", "url": "https://storage.example/photo", "date": "2026-01-15T12:00:00Z"}
    ]
  }
]

Examples:
    python scripts/import_legacy_portfolio.py C:\\secure\\archive.json --dry-run
    python scripts/import_legacy_portfolio.py C:\\secure\\archive.json --allowed-host storage.example
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import mimetypes
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

# Direct execution uses ``scripts`` as sys.path[0]; add the repository root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.constants import MONTHS
from app.db.database import SessionLocal
from app.models.legacy_portfolio_photo import LegacyPortfolioPhoto
from app.models.user import User
from app.services.s3 import is_configured, s3_path_legacy_archive, upload_to_s3

log = logging.getLogger("legacy-portfolio-import")
DEFAULT_ALLOWED_HOSTS = {"storage.leadteh.ru"}
MAX_PHOTO_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class SourcePhoto:
    user_id: int
    dialog_id: str
    filename: str
    url: str
    sent_at: datetime


def _parse_sent_at(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("photo date must be a non-empty ISO 8601 string")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_source_url(value: object, allowed_hosts: set[str]) -> str:
    if not isinstance(value, str):
        raise ValueError("photo URL must be a string")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or host not in allowed_hosts:
        raise ValueError("photo URL must use HTTPS and an explicitly allowed host")
    return value


def _stable_object_name(photo: SourcePhoto) -> str:
    suffix = Path(photo.filename).suffix.lower()
    if not suffix or len(suffix) > 10:
        suffix = ".jpg"
    digest = hashlib.sha256(photo.url.encode("utf-8")).hexdigest()[:20]
    return f"{digest}{suffix}"


def load_payload(path: Path, allowed_hosts: set[str]) -> list[SourcePhoto]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("payload root must be an array")

    result: list[SourcePhoto] = []
    for entry_index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"entry {entry_index} must be an object")
        user_id = entry.get("user_id")
        dialog_id = entry.get("dialog_id")
        photos = entry.get("photos")
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError(f"entry {entry_index} has an invalid user_id")
        if not isinstance(dialog_id, str) or not dialog_id.strip() or len(dialog_id) > 30:
            raise ValueError(f"entry {entry_index} has an invalid dialog_id")
        if not isinstance(photos, list):
            raise ValueError(f"entry {entry_index} photos must be an array")

        for photo_index, photo in enumerate(photos, start=1):
            if not isinstance(photo, dict):
                raise ValueError(f"entry {entry_index} photo {photo_index} must be an object")
            filename = photo.get("filename")
            if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
                raise ValueError(f"entry {entry_index} photo {photo_index} has an invalid filename")
            safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
            if not safe_filename:
                raise ValueError(f"entry {entry_index} photo {photo_index} has an invalid filename")
            result.append(
                SourcePhoto(
                    user_id=user_id,
                    dialog_id=dialog_id.strip(),
                    filename=safe_filename,
                    url=_validate_source_url(photo.get("url"), allowed_hosts),
                    sent_at=_parse_sent_at(photo.get("date")),
                )
            )
    return result


def _download_photo(client: httpx.Client, photo: SourcePhoto) -> tuple[bytes, str]:
    with client.stream("GET", photo.url, follow_redirects=False) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            guessed, _ = mimetypes.guess_type(photo.filename)
            if not guessed or not guessed.startswith("image/"):
                raise ValueError("source response is not an image")
            content_type = guessed
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > MAX_PHOTO_BYTES:
                raise ValueError("source image exceeds 25 MB")
            chunks.append(chunk)
    if total == 0:
        raise ValueError("source image is empty")
    return b"".join(chunks), content_type


def import_photos(
    db: Session,
    photos: list[SourcePhoto],
    *,
    dry_run: bool,
) -> dict[str, int]:
    counters = {"created": 0, "skipped_existing": 0, "skipped_no_user": 0, "failed": 0}
    users = {
        user.id: user
        for user in db.query(User).filter(User.id.in_({photo.user_id for photo in photos})).all()
    }

    if not dry_run and not is_configured():
        raise RuntimeError("S3 is not configured")

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for index, photo in enumerate(photos, start=1):
            user = users.get(photo.user_id)
            if user is None:
                counters["skipped_no_user"] += 1
                continue

            existing = (
                db.query(LegacyPortfolioPhoto.id)
                .filter(
                    LegacyPortfolioPhoto.user_id == photo.user_id,
                    LegacyPortfolioPhoto.dialog_id == photo.dialog_id,
                    LegacyPortfolioPhoto.sent_at == photo.sent_at,
                    LegacyPortfolioPhoto.original_filename == photo.filename,
                )
                .first()
            )
            if existing:
                counters["skipped_existing"] += 1
                continue

            if dry_run:
                counters["created"] += 1
                continue

            try:
                data, content_type = _download_photo(client, photo)
                month_num = photo.sent_at.month
                object_name = _stable_object_name(photo)
                s3_path = s3_path_legacy_archive(
                    user.vk_id, user.tariff, photo.sent_at.year, month_num, object_name
                )
                s3_url = upload_to_s3(s3_path, data, content_type)
                if not s3_url:
                    raise RuntimeError("S3 upload failed")
                db.add(
                    LegacyPortfolioPhoto(
                        user_id=user.id,
                        dialog_id=photo.dialog_id,
                        month=MONTHS[month_num - 1],
                        year=photo.sent_at.year,
                        original_filename=photo.filename,
                        s3_path=s3_path,
                        s3_url=s3_url,
                        sent_at=photo.sent_at,
                    )
                )
                db.commit()
                counters["created"] += 1
            except Exception:
                db.rollback()
                counters["failed"] += 1
                log.exception("Photo %d failed", index)
    return counters


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a legacy portfolio JSON archive")
    parser.add_argument("payload", type=Path, help="Path to the external JSON payload")
    parser.add_argument("--dry-run", action="store_true", help="Validate and count without network or writes")
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="HTTPS source hostname; repeat to allow more than one host",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    allowed_hosts = {host.strip().lower() for host in args.allowed_host if host.strip()}
    allowed_hosts = allowed_hosts or DEFAULT_ALLOWED_HOSTS

    try:
        photos = load_payload(args.payload, allowed_hosts)
        log.info("Validated %d archive photos", len(photos))
        db = SessionLocal()
        try:
            result = import_photos(db, photos, dry_run=args.dry_run)
        finally:
            db.close()
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1

    log.info("Import summary: %s", result)
    return 0 if result["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
