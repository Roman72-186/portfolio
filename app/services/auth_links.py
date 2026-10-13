import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.models.login_token import LoginToken
from app.models.telegram_link_token import TelegramLinkToken
from app.models.user import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Токен входа в 3D-лабораторию лежит в той же таблице, что и ссылки входа на
# сайт, — отличает его только `issued_by`. До 13.10.2026 погашение было одно на
# оба вида: токен, который лаборатория получает в адресе редиректа, открывал
# обычную сессию на сайте, а ссылка входа проходила проверку лаборатории
# (код-ревью 12.10.2026, P1 № 1). Теперь вызывающий называет вид, токен другого
# вида — «invalid», и выдача нового токена отзывает только токены своего вида.
SSO_ISSUED_BY = "3dlab-sso"
PURPOSE_LOGIN = "login"
PURPOSE_SSO = "sso"


def _is_sso(token: LoginToken) -> bool:
    return token.issued_by == SSO_ISSUED_BY


def _revoke_active(db: DBSession, *, user_id: int, sso: bool, now: datetime) -> None:
    """Отозвать живые токены пользователя того же вида, что выдаётся сейчас."""
    kind = (
        LoginToken.issued_by == SSO_ISSUED_BY
        if sso else LoginToken.issued_by.is_distinct_from(SSO_ISSUED_BY)
    )
    db.query(LoginToken).filter(
        LoginToken.user_id == user_id,
        LoginToken.used_at.is_(None),
        LoginToken.revoked_at.is_(None),
        LoginToken.expires_at > now,
        kind,
    ).update(
        {LoginToken.revoked_at: now},
        synchronize_session=False,
    )


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def next_manual_vk_id(db: DBSession) -> int:
    """Следующий синтетический vk_id для аккаунтов без настоящего VK ID
    (ручные сотрудники, ученики, заведённые через Telegram-бота). Держим их
    в отрицательном диапазоне — vk_id остаётся внутренним первичным ключом
    личности пользователя независимо от способа входа."""
    min_vk = db.query(func.min(User.vk_id)).scalar() or 0
    return min(min_vk - 1, -1)


def issue_one_time_login_link(
    db: DBSession,
    *,
    user: User,
    base_url: str,
    issued_by: str = "system",
) -> tuple[str, LoginToken]:
    now = _now()
    _revoke_active(db, user_id=user.id, sso=False, now=now)

    raw_token = secrets.token_urlsafe(32)
    login_token = LoginToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        issued_by=issued_by,
        expires_at=now + timedelta(minutes=settings.one_time_link_ttl_minutes),
    )
    db.add(login_token)
    db.commit()
    db.refresh(login_token)

    login_url = f"{base_url.rstrip('/')}/auth/link?token={quote(raw_token)}"
    return login_url, login_token


def issue_sso_token(
    db: DBSession,
    *,
    user: User,
    ttl_minutes: int,
) -> tuple[str, LoginToken]:
    """Issue a short-lived cross-service SSO token (no login URL built).

    Returns (raw_token, LoginToken). The caller builds the redirect URL.
    Revokes previous active SSO tokens only: a login link issued by staff
    must survive the student opening the lab.
    """
    now = _now()
    _revoke_active(db, user_id=user.id, sso=True, now=now)

    raw_token = secrets.token_urlsafe(32)
    login_token = LoginToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        issued_by=SSO_ISSUED_BY,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    db.add(login_token)
    db.commit()
    db.refresh(login_token)
    return raw_token, login_token


def consume_one_time_login_token(
    db: DBSession,
    *,
    raw_token: str,
    purpose: str = PURPOSE_LOGIN,
) -> tuple[LoginToken | None, User | None, str | None]:
    """Погасить токен вида `purpose`: `PURPOSE_LOGIN` — ссылка входа на сайт,
    `PURPOSE_SSO` — вход в 3D-лабораторию. Токен другого вида не гасится и
    отвечает «invalid», как несуществующий."""
    token_hash = _hash_token(raw_token)
    login_token = db.query(LoginToken).filter(LoginToken.token_hash == token_hash).first()
    if not login_token or _is_sso(login_token) != (purpose == PURPOSE_SSO):
        return None, None, "invalid"

    now = _now()
    updated = (
        db.query(LoginToken)
        .filter(
            LoginToken.id == login_token.id,
            LoginToken.used_at.is_(None),
            LoginToken.revoked_at.is_(None),
            LoginToken.expires_at > now,
        )
        .update({LoginToken.used_at: now}, synchronize_session=False)
    )
    if updated == 1:
        db.commit()
        db.expire_all()
        record = (
            db.query(LoginToken, User)
            .join(User, LoginToken.user_id == User.id)
            .filter(LoginToken.id == login_token.id)
            .first()
        )
        if not record:
            return None, None, "invalid"
        return record[0], record[1], None

    db.rollback()
    db.refresh(login_token)
    user = db.query(User).filter(User.id == login_token.user_id).first()
    if login_token.revoked_at is not None:
        return login_token, user, "revoked"
    if login_token.used_at is not None:
        return login_token, user, "used"
    if login_token.expires_at <= now:
        return login_token, user, "expired"
    return login_token, user, "invalid"


def issue_telegram_link_token(
    db: DBSession,
    *,
    user: User,
    issued_by: str = "system",
) -> tuple[str, TelegramLinkToken]:
    """Выпустить приглашение для действующего ученика привязать Telegram к
    его текущему аккаунту (используется вместо создания нового аккаунта)."""
    now = _now()
    db.query(TelegramLinkToken).filter(
        TelegramLinkToken.user_id == user.id,
        TelegramLinkToken.used_at.is_(None),
        TelegramLinkToken.revoked_at.is_(None),
        TelegramLinkToken.expires_at > now,
    ).update(
        {TelegramLinkToken.revoked_at: now},
        synchronize_session=False,
    )

    raw_token = secrets.token_urlsafe(32)
    link_token = TelegramLinkToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        issued_by=issued_by,
        expires_at=now + timedelta(hours=settings.telegram_link_ttl_hours),
    )
    db.add(link_token)
    db.commit()
    db.refresh(link_token)
    return raw_token, link_token


def consume_telegram_link_token(
    db: DBSession,
    *,
    raw_token: str,
) -> tuple[TelegramLinkToken | None, User | None, str | None]:
    token_hash = _hash_token(raw_token)
    link_token = db.query(TelegramLinkToken).filter(TelegramLinkToken.token_hash == token_hash).first()
    if not link_token:
        return None, None, "invalid"

    now = _now()
    updated = (
        db.query(TelegramLinkToken)
        .filter(
            TelegramLinkToken.id == link_token.id,
            TelegramLinkToken.used_at.is_(None),
            TelegramLinkToken.revoked_at.is_(None),
            TelegramLinkToken.expires_at > now,
        )
        .update({TelegramLinkToken.used_at: now}, synchronize_session=False)
    )
    if updated == 1:
        db.commit()
        db.expire_all()
        record = (
            db.query(TelegramLinkToken, User)
            .join(User, TelegramLinkToken.user_id == User.id)
            .filter(TelegramLinkToken.id == link_token.id)
            .first()
        )
        if not record:
            return None, None, "invalid"
        return record[0], record[1], None

    db.rollback()
    db.refresh(link_token)
    user = db.query(User).filter(User.id == link_token.user_id).first()
    if link_token.revoked_at is not None:
        return link_token, user, "revoked"
    if link_token.used_at is not None:
        return link_token, user, "used"
    if link_token.expires_at <= now:
        return link_token, user, "expired"
    return link_token, user, "invalid"
