"""Shared retry policy for outbound HTTP calls to third-party services.

Transient gateway errors (502/503/504) and network-level timeouts/transport
errors are retried with a short linear backoff — the policy already proven
for VK API calls (see log incident 2026-06-19: groups.isMember 502/504
burst, VK healthy seconds later). Centralised here so other integrations
(n8n, Google Drive) get the same resilience instead of failing outright on
a brief upstream blip.
"""
import asyncio
import logging
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUSES = {502, 503, 504}
MAX_ATTEMPTS = 3
RETRY_BACKOFF = 0.5  # seconds, multiplied by attempt number


async def request_with_retry(
    make_request: Callable[[], Awaitable[httpx.Response]],
    *,
    label: str,
    max_attempts: int = MAX_ATTEMPTS,
) -> httpx.Response:
    """Run ``make_request``, retrying transient gateway errors and network timeouts.

    ``make_request`` must be re-callable (e.g. a closure issuing the same
    request each time), since a retried attempt re-sends it from scratch.
    Non-transient HTTP errors (4xx, other 5xx) are returned as-is for the
    caller to handle via ``resp.raise_for_status()``.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = await make_request()
            if resp.status_code in RETRY_STATUSES and attempt < max_attempts:
                logger.warning(
                    "%s transient HTTP %s (attempt %s/%s), retrying",
                    label, resp.status_code, attempt, max_attempts,
                )
                await asyncio.sleep(RETRY_BACKOFF * attempt)
                continue
            return resp
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_attempts:
                logger.warning(
                    "%s network error %s (attempt %s/%s), retrying",
                    label, type(exc).__name__, attempt, max_attempts,
                )
                await asyncio.sleep(RETRY_BACKOFF * attempt)
                continue
            raise
    raise last_exc  # pragma: no cover — loop always returns or raises above
