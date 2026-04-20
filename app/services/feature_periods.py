"""Service for checking feature period availability."""
from functools import lru_cache
from threading import Lock

from sqlalchemy.orm import Session as DBSession

from app.constants import FEATURE_LABELS
from app.models.feature_period import FeaturePeriod
from app.services.tz import today_msk

_cache: dict = {}
_cache_lock = Lock()
CACHE_TTL_SECONDS = 300  # 5 minutes


def _cache_key(feature: str) -> str:
    return f"feature:{feature}"


def is_feature_available(db: DBSession, feature: str) -> tuple[bool, str | None]:
    """
    Returns (available, message_if_unavailable).
    Cached for 5 minutes.
    """
    import time
    now = time.monotonic()
    key = _cache_key(feature)

    with _cache_lock:
        if key in _cache:
            value, ts = _cache[key]
            if now - ts < CACHE_TTL_SECONDS:
                return value

    today = today_msk()
    period = (
        db.query(FeaturePeriod)
        .filter(
            FeaturePeriod.feature == feature,
            FeaturePeriod.is_active == True,
            FeaturePeriod.start_date <= today,
            FeaturePeriod.end_date >= today,
        )
        .first()
    )

    label = FEATURE_LABELS.get(feature, feature)
    if period:
        result: tuple[bool, str | None] = (True, None)
    else:
        result = (False, f"«{label}» сейчас недоступно. Администратор откроет доступ в нужное время.")

    with _cache_lock:
        _cache[key] = (result, time.monotonic())

    return result


def get_active_period(db: DBSession, feature: str) -> FeaturePeriod | None:
    """Возвращает активный FeaturePeriod на сегодня (MSK) или None."""
    today = today_msk()
    return (
        db.query(FeaturePeriod)
        .filter(
            FeaturePeriod.feature == feature,
            FeaturePeriod.is_active == True,
            FeaturePeriod.start_date <= today,
            FeaturePeriod.end_date >= today,
        )
        .first()
    )


def invalidate_feature_cache(feature: str | None = None) -> None:
    """Call after creating/updating/deactivating a period."""
    with _cache_lock:
        if feature:
            _cache.pop(_cache_key(feature), None)
        else:
            _cache.clear()
