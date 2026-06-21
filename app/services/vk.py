import asyncio
import base64
import hashlib
import logging
import os
from urllib.parse import urlencode

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Shared persistent client — initialised/closed through app lifespan (see main.py).
_client: httpx.AsyncClient | None = None


async def init_client() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=15.0)


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15.0)
    return _client


def generate_code_verifier() -> str:
    """Generate a PKCE code verifier (URL-safe, 43 chars)."""
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def generate_code_challenge(verifier: str) -> str:
    """Derive PKCE code challenge (S256) from verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# VK API edge intermittently returns 502/503/504 and recovers within seconds.
# Retry transient gateway errors and network timeouts so a brief VK blip during
# a login does not surface as a failed membership check (denied.html). See log
# incident 2026-06-19: groups.isMember 502/504 burst, VK healthy seconds later.
_VK_RETRY_STATUSES = {502, 503, 504}
_VK_MAX_ATTEMPTS = 3
_VK_RETRY_BACKOFF = 0.5  # seconds, multiplied by attempt number


async def _vk_api_get(method: str, params: dict) -> dict:
    """Make a VK API GET request using the shared persistent client.

    Retries transient gateway errors (502/503/504) and network timeouts with a
    short linear backoff. Non-transient HTTP errors and VK API-level errors
    (returned as 200 with an ``error`` body) are not retried here.
    """
    client = await _get_client()
    url = f"https://api.vk.com/method/{method}"
    last_exc: Exception | None = None
    for attempt in range(1, _VK_MAX_ATTEMPTS + 1):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code in _VK_RETRY_STATUSES and attempt < _VK_MAX_ATTEMPTS:
                logger.warning(
                    "VK %s transient HTTP %s (attempt %s/%s), retrying",
                    method, resp.status_code, attempt, _VK_MAX_ATTEMPTS,
                )
                await asyncio.sleep(_VK_RETRY_BACKOFF * attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < _VK_MAX_ATTEMPTS:
                logger.warning(
                    "VK %s network error %s (attempt %s/%s), retrying",
                    method, type(exc).__name__, attempt, _VK_MAX_ATTEMPTS,
                )
                await asyncio.sleep(_VK_RETRY_BACKOFF * attempt)
                continue
            raise
    # Loop only falls through when the last attempt was a retryable status code.
    if last_exc is not None:
        raise last_exc
    resp.raise_for_status()
    return resp.json()


def get_authorize_url(state: str, code_challenge: str) -> str:
    """Build VK ID OAuth authorize URL with PKCE (S256)."""
    params = {
        "client_id": settings.vk_app_id,
        "redirect_uri": settings.vk_redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": "groups",
    }
    return f"https://id.vk.com/authorize?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str, device_id: str) -> dict:
    """Exchange authorization code for access token via VK ID (PKCE)."""
    client = await _get_client()
    resp = await client.post(
        "https://id.vk.com/oauth2/auth",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.vk_app_id,
            "client_secret": settings.vk_app_secret,
            "redirect_uri": settings.vk_redirect_uri,
            "code_verifier": code_verifier,
            "device_id": device_id,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    logger.info(
        "VK ID token exchange OK, user_id=%s, scope=%s",
        data.get("user_id"), data.get("scope"),
    )
    return data


async def get_user_info(access_token: str, user_id: int) -> dict:
    """Get VK user profile info."""
    data = await _vk_api_get("users.get", {
        "user_ids": user_id,
        "fields": "photo_200",
        "access_token": access_token,
        "v": "5.199",
    })

    if "error" in data:
        logger.error("VK users.get error: %s", data["error"])
        raise RuntimeError(f"VK API error: {data['error'].get('error_msg', 'unknown')}")

    if not data.get("response"):
        raise RuntimeError("VK API вернул пустой список пользователей")
    user = data["response"][0]
    first_name = user.get("first_name", "")
    last_name = user.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip()
    return {
        "vk_id": user["id"],
        "name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "photo_url": user.get("photo_200"),
    }


async def check_group_membership(access_token: str, user_id: int, group_id: int) -> bool | None:
    """Check if user is a member of the specified VK group.

    Returns True/False for a definite VK answer, or None if the check could
    not be completed (network error, HTTP error, VK API error). Callers must
    not treat None as a confirmed non-membership.
    """
    try:
        data = await _vk_api_get("groups.isMember", {
            "group_id": group_id,
            "user_id": user_id,
            "access_token": access_token,
            "v": "5.199",
        })
    except Exception as exc:
        logger.warning("VK groups.isMember request failed: %s", exc)
        return None

    logger.info(
        "VK groups.isMember user_id=%s group_id=%s -> %s",
        user_id, group_id, data,
    )

    if "error" in data:
        logger.warning("VK groups.isMember error: %s", data["error"])
        return None

    response = data.get("response")
    if isinstance(response, dict):
        return response.get("member") == 1
    return response == 1
