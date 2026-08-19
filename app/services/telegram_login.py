"""Telegram Login (OIDC) — стандартный Authorization Code Flow с PKCE.

Заменяет прежний вход через deep-link на бота (`t.me/<bot>?start=...`):
пользователь подтверждает вход на обычной веб-странице `oauth.telegram.org`,
без перехода в нативное приложение Telegram — это и чинит вход из
iOS-приложения, добавленного на экран «Домой» (standalone PWA), где
переход в другое приложение и обратно не сохранял сессию.

Эндпоинты и алгоритм проверены по официальной документации
`core.telegram.org/bots/telegram-login` (2026-08-19), не по памяти.
Scope `telegram:bot_access` даёт боту право писать пользователю без
отдельного `/start` — второй канал уведомлений (Telegram) настраивается
тем же входом, без дополнительного шага.
"""
import base64
import logging
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings
from app.services.vk import generate_code_verifier, generate_code_challenge  # noqa: F401 — переиспользуем PKCE-хелперы

logger = logging.getLogger(__name__)

_AUTH_URL = "https://oauth.telegram.org/auth"
_TOKEN_URL = "https://oauth.telegram.org/token"
_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
_ISSUER = "https://oauth.telegram.org"
_SCOPE = "openid profile telegram:bot_access"

_client: httpx.AsyncClient | None = None
_jwks_client: PyJWKClient | None = None


async def init_client() -> None:
    global _client, _jwks_client
    _client = httpx.AsyncClient(timeout=15.0)
    _jwks_client = PyJWKClient(_JWKS_URL)


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


def get_authorize_url(state: str, code_challenge: str) -> str:
    """Построить authorize-URL Telegram Login с PKCE (S256)."""
    params = {
        "client_id": settings.telegram_login_client_id,
        "redirect_uri": settings.telegram_login_redirect_uri,
        "response_type": "code",
        "scope": _SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str) -> dict:
    """Обменять authorization code на id_token (PKCE, Basic-авторизация client_id:secret)."""
    client = await _get_client()
    basic = base64.b64encode(
        f"{settings.telegram_login_client_id}:{settings.telegram_login_client_secret}".encode()
    ).decode()
    resp = await client.post(
        _TOKEN_URL,
        headers={"Authorization": f"Basic {basic}"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.telegram_login_redirect_uri,
            "client_id": settings.telegram_login_client_id,
            "code_verifier": code_verifier,
        },
    )
    resp.raise_for_status()
    return resp.json()


def verify_id_token(id_token: str) -> dict:
    """Проверить подпись id_token (RS256 через JWKS) и claims iss/aud/exp.

    Синхронная сеть внутри (PyJWKClient) — вызывать через
    ``asyncio.to_thread`` из async-роута, чтобы не блокировать event loop.
    """
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(_JWKS_URL)
    signing_key = _jwks_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.telegram_login_client_id,
        issuer=_ISSUER,
    )
