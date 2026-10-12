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
import hashlib
import logging
import os
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings


def generate_code_verifier() -> str:
    """PKCE code verifier (URL-safe, 43 символа). Жил в `vk.py` до удаления VK
    29.09.2026 — Telegram-вход им пользовался и пользуется."""
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def generate_code_challenge(verifier: str) -> str:
    """PKCE code challenge (S256) из verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

logger = logging.getLogger(__name__)

_AUTH_URL = "https://oauth.telegram.org/auth"
_TOKEN_URL = "https://oauth.telegram.org/token"
_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
_ISSUER = "https://oauth.telegram.org"
_SCOPE = "openid profile telegram:bot_access"

_client: httpx.AsyncClient | None = None
_jwks_client: PyJWKClient | None = None


class _JWKClient(PyJWKClient):
    """Ключи Telegram скачивает httpx, а не urllib внутри PyJWT.

    `oauth.telegram.org` примерно на каждый третий запрос отдаёт JWKS сжатым
    (`Content-Encoding: gzip`), даже когда сжатие не просили. urllib его не
    распаковывает, PyJWT разбирает сжатые байты (начало 1f 8b) как JSON и падает с
    «'utf-8' codec can't decode byte 0x8b» — вход через Telegram не проходит,
    со второй попытки проходит (прод: 4–6 в день, 13 за вечер переезда
    11.10.2026, когда входили все разом). httpx распаковывает сам; кеш и
    обработка ошибок остаются у PyJWKClient.
    """

    def fetch_data(self):
        try:
            resp = httpx.get(self.uri, headers=self.headers, timeout=self.timeout)
            resp.raise_for_status()
            jwk_set = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise jwt.PyJWKClientConnectionError(
                f'Fail to fetch data from the url, err: "{exc}"'
            ) from exc
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(jwk_set)
        return jwk_set


async def init_client() -> None:
    global _client, _jwks_client
    _client = httpx.AsyncClient(timeout=15.0)
    _jwks_client = _JWKClient(_JWKS_URL)


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
        _jwks_client = _JWKClient(_JWKS_URL)
    signing_key = _jwks_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.telegram_login_client_id,
        issuer=_ISSUER,
    )
