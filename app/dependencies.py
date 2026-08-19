import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable

log = logging.getLogger(__name__)

from fastapi import Request, Response, Depends, HTTPException, Header, Form
from sqlalchemy.orm import Session as DBSession, joinedload

from app.config import settings
from app.csrf import validate_csrf_token
from app.db.database import get_db
from app.models.session import Session
from app.models.user import User


def _as_utc(value: datetime) -> datetime:
    """Normalize timestamps returned by SQLite and PostgreSQL to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_current_user(
    request: Request,
    response: Response,
    db: Annotated[DBSession, Depends(get_db)],
) -> dict:
    """Extract and validate session from cookie, join with User and Role."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        log.warning(
            "Auth 401 'Нет сессии' | path=%s | ua=%s",
            request.url.path, request.headers.get("user-agent", "")[:120],
        )
        raise HTTPException(status_code=401, detail="Нет сессии")

    # Always validate auth-critical state against DB. Cached session payloads can
    # outlive role changes, account blocks, deletes, or session revocation.
    row = (
        db.query(Session, User)
        .join(User, Session.user_id == User.id)
        .options(joinedload(User.role))
        .filter(Session.id == session_id, Session.is_active == True)
        .first()
    )

    if not row:
        log.warning(
            "Auth 401 'Сессия не найдена' | path=%s | session_id_prefix=%s | ua=%s",
            request.url.path, session_id[:8], request.headers.get("user-agent", "")[:120],
        )
        raise HTTPException(status_code=401, detail="Сессия не найдена")

    session, user = row

    now = datetime.now(timezone.utc)
    expires_at = _as_utc(session.expires_at)
    if expires_at < now:
        log.warning(
            "Auth 401 'Сессия истекла' | path=%s | session_id_prefix=%s | user_id=%s | "
            "expires_at=%s | now=%s | overdue_sec=%.1f",
            request.url.path, session_id[:8], user.id,
            expires_at.isoformat(), now.isoformat(),
            (now - expires_at).total_seconds(),
        )
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="Сессия истекла")

    # Sliding session: once the session is past the halfway point of its TTL,
    # extend it and refresh the cookie's max-age. Without this, a long-lived
    # but actively-used tab (e.g. the 4h mock exam) can outlive a fixed
    # session_id cookie expiry and the browser drops the cookie mid-session.
    ttl = timedelta(hours=settings.session_ttl_hours)
    if expires_at - now < ttl / 2:
        session.expires_at = now + ttl
        db.commit()
        response.set_cookie(
            key="session_id",
            value=session.id,
            httponly=True,
            samesite="lax",
            max_age=settings.session_ttl_hours * 3600,
            secure=True,
            path="/",
        )

    if user.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Аккаунт удалён")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")

    role = user.role
    role_rank = role.rank if role else 0
    role_name = role.name if role else None
    is_admin = role_rank >= 4 if role else user.is_admin

    if role_rank == 0 and not user.is_admin and not user.is_group_member:
        raise HTTPException(status_code=403, detail="Доступ возможен только участникам группы")

    result = {
        "session_id": session.id,
        "impersonated_by_id": session.impersonated_by_id,
        "user_id": user.id,
        "vk_id": user.vk_id,
        "name": user.name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "parent_phone": user.parent_phone,
        "about": user.about,
        "profile_completed": user.profile_completed,
        "portfolio_do_completed": user.portfolio_do_completed,
        "drive_folder_id": user.drive_folder_id,
        "curator_id": user.curator_id,
        "tariff": user.tariff,
        "photo_url": user.photo_url,
        "is_admin": is_admin,
        "is_group_member": user.is_group_member,
        "last_vk_check_at": user.last_vk_check_at,
        "tg_username": user.tg_username,
        "enrollment_year": user.enrollment_year,
        "university_year": user.university_year,
        "past_tariffs": user.past_tariffs,
        "course_periods": user.course_periods,
        "lessons_count": user.lessons_count,
        "enrolled_at": user.enrolled_at,
        "created_at": user.created_at,
        "role_name": role_name,
        "role_rank": role_rank,
    }

    return result


def require_role(minimum_rank: int) -> Callable:
    """Factory: returns a FastAPI dependency that requires role rank >= minimum_rank."""
    def _dep(user: Annotated[dict, Depends(get_current_user)]) -> dict:
        if user["role_rank"] < minimum_rank:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user
    return _dep


# Role aliases for use in route dependencies
require_student    = require_role(1)
require_curator    = require_role(2)
require_moderator  = require_role(3)
require_admin_role = require_role(4)
require_superadmin = require_role(5)


def require_learning_content_access(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Allow students with confirmed group access and staff preview users.

    Telegram live membership verification will become the owner check later.
    Until then, student access fails closed on the existing membership flag.
    """
    role_rank = user.get("role_rank", 0)
    if role_rank < 1:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    if role_rank == 1 and not user.get("is_group_member"):
        raise HTTPException(
            status_code=403,
            detail="Доступ к учебным материалам доступен только участникам группы",
        )
    return user


def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    """Backward-compatible admin check (rank >= 4)."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return user


def require_internal_api_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.internal_api_token:
        raise HTTPException(status_code=503, detail="Internal API token is not configured")
    if not secrets.compare_digest(x_internal_token or "", settings.internal_api_token):
        raise HTTPException(status_code=401, detail="Invalid internal API token")


def require_telegram_webhook_secret(
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> None:
    """Telegram шлёт этот заголовок на каждый апдейт, если secret_token задан
    при setWebhook (scripts/set_telegram_webhook.py). Замена rate-limit'а по
    IP: вебхук — сервер-серверный вызов с общих IP Telegram, лимит по IP там
    бессмыслен и может случайно резать легитимный трафик."""
    if not settings.telegram_webhook_secret:
        raise HTTPException(status_code=503, detail="Telegram webhook secret is not configured")
    if not secrets.compare_digest(x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")


def require_csrf(
    request: Request,
    csrf_token: Annotated[str, Form(alias="csrf_token")] = "",
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """Validate CSRF token for state-changing POST requests.

    Accepts token either via form field `csrf_token` (classic form POST)
    or via header `X-CSRF-Token` (AJAX/fetch with multipart FormData).
    """
    session_id = request.cookies.get("session_id", "")
    token = x_csrf_token or csrf_token
    if not validate_csrf_token(session_id, token):
        log.warning(
            "CSRF validation failed | path=%s | has_session=%s | has_token=%s | token_prefix=%s",
            request.url.path,
            bool(session_id),
            bool(token),
            token[:10] if token else "(empty)",
        )
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен. Обновите страницу и попробуйте снова.")


def require_csrf_header(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """Validate CSRF for JSON endpoints without changing their body media type."""
    session_id = request.cookies.get("session_id", "")
    token = x_csrf_token or ""
    if not validate_csrf_token(session_id, token):
        log.warning(
            "CSRF header validation failed | path=%s | has_session=%s | has_token=%s",
            request.url.path,
            bool(session_id),
            bool(token),
        )
        raise HTTPException(
            status_code=403,
            detail="Неверный CSRF-токен. Обновите страницу и попробуйте снова.",
        )


def require_lab3d_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.lab3d_internal_token:
        raise HTTPException(status_code=503, detail="3D Lab SSO token is not configured")
    if not secrets.compare_digest(x_internal_token or "", settings.lab3d_internal_token):
        raise HTTPException(status_code=401, detail="Invalid 3D Lab token")
