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


async def _vk_api_get(method: str, params: dict) -> dict:
    """Make a VK API GET request using the shared persistent client."""
    client = await _get_client()
    resp = await client.get(f"https://api.vk.com/method/{method}", params=params)
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
    logger.info("VK ID token exchange OK, user_id=%s", data.get("user_id"))
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


async def check_group_membership(access_token: str, user_id: int, group_id: int) -> bool:
    """Check if user is a member of the specified VK group."""
    try:
        data = await _vk_api_get("groups.isMember", {
            "group_id": group_id,
            "user_id": user_id,
            "access_token": access_token,
            "v": "5.199",
        })
    except Exception as exc:
        logger.warning("VK groups.isMember request failed: %s", exc)
        return False

    if "error" in data:
        logger.warning("VK groups.isMember error: %s", data["error"])
        return False

    return data.get("response") == 1
