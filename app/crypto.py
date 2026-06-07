"""Transparent field-level encryption for PII columns.

Uses Fernet (AES-128-CBC + HMAC-SHA256) via the `cryptography` library.
The key is derived from `settings.session_secret` using PBKDF2 so that
no additional env var is needed (session_secret is already treated as
a high-value secret).

Usage in models:
    from app.crypto import EncryptedString
    phone: Mapped[str | None] = mapped_column(EncryptedString(30), nullable=True)

All existing code reading/writing these fields works unchanged — encryption
and decryption happen inside SQLAlchemy's type system.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String, TypeDecorator

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_legacy_fernet: Fernet | None = None


def _derive_fernet(secret: str) -> Fernet:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode(),
        b"portfolio-pii-encryption-salt",
        iterations=100_000,
    )
    return Fernet(base64.urlsafe_b64encode(dk[:32]))


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        from app.config import settings
        _fernet = _derive_fernet(settings.pii_encryption_secret or settings.session_secret)
    return _fernet


def _get_legacy_fernet() -> Fernet | None:
    global _legacy_fernet
    from app.config import settings
    if not settings.pii_encryption_secret:
        return None
    if _legacy_fernet is None:
        _legacy_fernet = _derive_fernet(settings.session_secret)
    return _legacy_fernet


class EncryptedString(TypeDecorator):
    """SQLAlchemy type that transparently encrypts/decrypts string values.

    Stores ciphertext (Fernet token, ~150 chars for short inputs) in a
    TEXT column.  On read, decrypts back to plaintext.  NULL values pass
    through unchanged.

    If decryption fails (e.g. key rotated, legacy plaintext row), the raw
    value is returned as-is and a warning is logged.  This makes migration
    from plaintext columns safe — old rows remain readable until re-saved.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int | None = None):
        # Store as TEXT to accommodate Fernet token length
        super().__init__()

    def process_bind_param(self, value, dialect):
        """Encrypt before INSERT/UPDATE."""
        if value is None:
            return None
        f = _get_fernet()
        return f.encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value, dialect):
        """Decrypt after SELECT."""
        if value is None:
            return None
        token = value.encode("ascii")
        for f in (_get_fernet(), _get_legacy_fernet()):
            if f is None:
                continue
            try:
                return f.decrypt(token).decode("utf-8")
            except InvalidToken:
                continue
        logger.debug("Could not decrypt value, returning as plaintext (legacy row)")
        return value
