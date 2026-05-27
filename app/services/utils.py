import io
import logging
from collections import defaultdict
from datetime import datetime, timezone

from app.constants import MONTH_TO_NUM

logger = logging.getLogger(__name__)


def compress_image(data: bytes, max_px: int = 1600, quality: int = 82) -> bytes:
    """Resize and compress an image to reduce file size.

    - Downscales so the longest side is at most max_px (default 1920).
    - Converts to JPEG at the given quality (default 85).
    - Returns original bytes untouched if PIL is unavailable or image is already small.
    """
    try:
        from PIL import Image, ImageFile
    except ImportError:
        return data

    # Register HEIC/HEIF decoder if available (iPhone default format).
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    try:
        # Mobile galleries sometimes hand us slightly truncated JPEGs that browsers
        # can still display. Pillow can recover these if we opt in.
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        img = Image.open(io.BytesIO(data))
        img.load()

        # Convert to RGB (handles RGBA PNG, CMYK, palette mode, etc.)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_px:
            ratio = max_px / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.BILINEAR)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        compressed = buf.getvalue()
    except Exception as exc:
        logger.warning("Image compression skipped: %s", exc)
        return data

    # Only use compressed version if it's actually smaller
    return compressed if len(compressed) < len(data) else data


def study_duration_text(enrolled_at: datetime) -> str:
    """Return a human-readable study duration: '1 г. 2 мес. 3 нед.' / '5 мес. 1 нед.' / '2 нед.'"""
    now = datetime.now(timezone.utc)
    if enrolled_at.tzinfo is None:
        enrolled_at = enrolled_at.replace(tzinfo=timezone.utc)
    delta_days = max(0, (now - enrolled_at).days)

    total_months = int(delta_days / 30.44)
    years = total_months // 12
    months = total_months % 12
    remaining_days = delta_days - int(total_months * 30.44)
    weeks = remaining_days // 7

    parts = []
    if years > 0:
        parts.append(f"{years} г.")
    if months > 0:
        parts.append(f"{months} мес.")
    if weeks > 0:
        parts.append(f"{weeks} нед.")

    if not parts:
        return "менее недели"
    return " ".join(parts)


def has_case_growth(works: list) -> bool:
    """True если по любому subject есть рост score между двумя пробниками одного ученика.

    Использует in-memory список Work — никаких SQL. Игнорирует работы без score,
    работы не типа mock_exam, и группы из одного пробника.
    """
    by_subject: dict[str, list] = defaultdict(list)
    for w in works:
        if getattr(w, "work_type", None) != "mock_exam":
            continue
        if w.score is None or not w.subject:
            continue
        by_subject[w.subject].append(w)

    for items in by_subject.values():
        if len(items) < 2:
            continue
        def _key(w):
            mnum = MONTH_TO_NUM.get(w.month, 0)
            ts = w.scored_at or w.created_at or datetime.min.replace(tzinfo=timezone.utc)
            return (w.year or 0, mnum, ts)

        ordered = sorted(items, key=_key)
        prev = ordered[0].score
        for w in ordered[1:]:
            if w.score is not None and prev is not None and float(w.score) > float(prev):
                return True
            prev = w.score
    return False


def group_works(works: list) -> list[dict]:
    """Group Work records by (year, month), compute per-group average score.

    Returns a list of dicts sorted chronologically:
      {"year": int, "month": str, "works": list, "monthly_avg": int|None, "total": int}
    """
    groups: dict[tuple, list] = defaultdict(list)
    for w in works:
        groups[(w.year, w.month)].append(w)

    result = []
    for (year, month), items in sorted(
        groups.items(),
        key=lambda kv: (kv[0][0], MONTH_TO_NUM.get(kv[0][1], 99)),
        reverse=True,  # последние месяцы первыми
    ):
        graded = [w for w in items if w.score is not None]
        monthly_avg = (
            round(sum(float(w.score) for w in graded) / len(graded))
            if graded else None
        )
        result.append({
            "year": year,
            "month": month,
            "works": sorted(items, key=lambda w: w.created_at, reverse=True),  # новые первыми
            "monthly_avg": monthly_avg,
            "total": len(items),
        })
    return result
