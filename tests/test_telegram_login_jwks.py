"""Ключи Telegram Login приходят сжатыми — вход всё равно проходит.

`oauth.telegram.org` примерно на каждый третий запрос отдаёт JWKS с
`Content-Encoding: gzip`, даже без запроса сжатия. Загрузчик PyJWT (urllib)
такой ответ не распаковывал, и вход падал с «'utf-8' codec can't decode byte
0x8b» (прод: 13 раз за вечер переезда 11.10.2026). Теперь ключи качает httpx.
"""
import gzip
import json
import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.services import telegram_login


def _signed_token_and_jwks():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "k1", "alg": "RS256", "use": "sig"})
    token = jwt.encode(
        {
            "iss": "https://oauth.telegram.org",
            "aud": "123456",
            "sub": "42",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        },
        key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    return token, {"keys": [jwk]}


def test_gzipped_jwks_does_not_break_login(monkeypatch):
    token, jwks = _signed_token_and_jwks()
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return httpx.Response(
            200,
            content=gzip.compress(json.dumps(jwks).encode()),
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(telegram_login.httpx, "get", fake_get)
    monkeypatch.setattr(telegram_login, "_jwks_client", None)
    monkeypatch.setattr(settings, "telegram_login_client_id", "123456")

    claims = telegram_login.verify_id_token(token)

    assert claims["sub"] == "42"
    assert calls == ["https://oauth.telegram.org/.well-known/jwks.json"]


def test_jwks_network_error_is_a_pyjwt_connection_error(monkeypatch):
    """Сбой сети остаётся ошибкой PyJWT: роут входа ловит её как и раньше."""
    token, _ = _signed_token_and_jwks()

    def broken_get(url, headers=None, timeout=None):
        raise httpx.ConnectError("нет сети", request=httpx.Request("GET", url))

    monkeypatch.setattr(telegram_login.httpx, "get", broken_get)
    monkeypatch.setattr(telegram_login, "_jwks_client", None)

    try:
        telegram_login.verify_id_token(token)
    except jwt.PyJWKClientError:
        pass
    else:
        raise AssertionError("ожидалась ошибка PyJWT")
