import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, HTTPException, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import bcrypt as _bcrypt_lib
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session, pop_vk_pkce, set_vk_pkce
from app.config import settings
from app.db.database import get_db
from app.dependencies import get_current_user, require_internal_api_token, require_lab3d_token
from app.limiter import limiter
from app.models.role import Role
from app.tmpl import templates
from app.models.session import Session
from app.models.user import User
from app.services.auth_links import consume_one_time_login_token, issue_one_time_login_link, issue_sso_token
from app.services.vk import (
    get_authorize_url, exchange_code, get_user_info, check_group_membership,
    generate_code_verifier, generate_code_challenge,
)
from app.services import drive as drive_service

logger = logging.getLogger(__name__)

router = APIRouter()

# PKCE fallback cookie signer. Primary PKCE storage lives in Redis.

_signer = URLSafeTimedSerializer(settings.session_secret)


class InternalIssueLinkRequest(BaseModel):
    vk_id: int
    name: str
    tariff: str | None = None
    photo_url: str | None = None
    is_group_member: bool = True


def _vk_login_enabled() -> bool:
    return bool(settings.vk_app_id and settings.vk_app_secret and settings.vk_group_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _render_login(request: Request, error: str | None = None):
    context = {
        "request": request,
        "vk_login_enabled": _vk_login_enabled(),
    }
    if error:
        context["error"] = error
    return templates.TemplateResponse("login.html", context)


def _load_pkce_cookie(request: Request) -> tuple[dict | None, str | None]:
    """Load signed PKCE cookie for temporary backward-compat fallback."""
    pkce_cookie = request.cookies.get("pkce_cv")
    if not pkce_cookie:
        logger.warning("VK callback: pkce_cv cookie missing (cookies=%s)", list(request.cookies.keys()))
        return None, "Ошибка сессии. Попробуйте снова или очистите cookies."
    try:
        pkce_data = _signer.loads(pkce_cookie, max_age=300)
        if not isinstance(pkce_data, dict):
            raise KeyError("pkce cookie payload is not a dict")
        _ = pkce_data["cv"]
        _ = pkce_data["st"]
    except SignatureExpired:
        logger.warning("VK callback: pkce_cv cookie expired (>5 min since login click)")
        return None, "Ссылка истекла — вы слишком долго авторизовывались. Попробуйте снова."
    except (BadSignature, KeyError) as exc:
        logger.warning("VK callback: pkce_cv bad signature or key: %s", exc)
        return None, "Ссылка истекла. Попробуйте снова."
    return pkce_data, None


def _public_base_url(request: Request) -> str:
    if settings.domain:
        return f"https://{settings.domain}"
    return str(request.base_url).rstrip("/")


def _upsert_user(
    db: DBSession,
    *,
    vk_id: int,
    name: str,
    first_name: str | None = None,
    last_name: str | None = None,
    photo_url: str | None = None,
    tariff: str | None = None,
    is_group_member: bool | None = None,
    mark_vk_checked: bool = False,
) -> User:
    now = _now()
    user = db.query(User).filter(User.vk_id == vk_id).first()
    if user:
        if user.deleted_at is not None:
            user.deleted_at = None
            user.is_active = True
            user.profile_completed = False
        user.name = name
        if first_name is not None:
            user.first_name = first_name
        if last_name is not None:
            user.last_name = last_name
        if photo_url is not None:
            user.photo_url = photo_url
        if tariff:
            user.tariff = tariff
        if is_group_member is not None:
            user.is_group_member = is_group_member
        if mark_vk_checked:
            user.last_vk_check_at = now
        return user

    student_role = db.query(Role).filter(Role.name == "ученик").first()
    user = User(
        vk_id=vk_id,
        name=name,
        first_name=first_name,
        last_name=last_name,
        photo_url=photo_url,
        tariff=tariff or "УВЕРЕННЫЙ",
        is_group_member=bool(is_group_member),
        last_vk_check_at=now if mark_vk_checked else None,
        role_id=student_role.id if student_role else None,
    )
    db.add(user)
    db.flush()
    return user


def _create_session_response(db: DBSession, user: User) -> RedirectResponse:
    session = Session(
        user_id=user.id,
        expires_at=_now() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    db.commit()

    response = RedirectResponse("/cabinet", status_code=302)
    response.set_cookie(
        key="session_id",
        value=session.id,
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        secure=True,
        path="/",
    )
    return response


@router.get("/", response_class=HTMLResponse)
async def entry_point(
    request: Request,
    db: Annotated[DBSession, Depends(get_db)],
    error: str | None = None,
):
    session_id = request.cookies.get("session_id")
    if session_id:
        session_row = db.query(Session, User).join(User, Session.user_id == User.id).filter(
            Session.id == session_id,
            Session.is_active == True,
        ).first()
        if session_row:
            session, user = session_row
            if (
                session.expires_at > _now()
                and user.is_active
                and (
                    user.is_admin
                    or user.is_group_member
                    or (user.role and user.role.rank >= 2)
                )
            ):
                return RedirectResponse("/cabinet", status_code=302)

    if error == "session_expired":
        error = "Сессия истекла, войдите снова"
    return _render_login(request, error)


@router.get("/auth/vk/login")
@limiter.limit("20/minute")
async def vk_login(request: Request):
    if not _vk_login_enabled():
        return RedirectResponse("/?error=VK-вход пока не настроен", status_code=302)

    # state — plain random token, no dots (VK mangles itsdangerous signed strings)
    state = secrets.token_urlsafe(32)
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    # Primary PKCE storage is server-side so mobile "open in VK app" survives.
    stored_in_redis = set_vk_pkce(state, code_verifier, ttl=300)
    if not stored_in_redis:
        logger.warning("VK login: failed to store PKCE in Redis for state=%s", state[:12])

    # Keep a versioned signed cookie only as rollout fallback for pre-deploy flows.
    pkce_signed = _signer.dumps({"cv": code_verifier, "st": state, "v": 2})
    url = get_authorize_url(state, code_challenge)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "pkce_cv", pkce_signed,
        httponly=True, secure=True, samesite="lax",
        max_age=300, path="/",
    )
    return response


@router.get("/auth/vk/callback", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def vk_callback(
    request: Request,
    background: BackgroundTasks,
    db: Annotated[DBSession, Depends(get_db)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    device_id: str | None = None,
):
    if error or not code:
        logger.warning("VK callback error: %s", error)
        return _render_login(request, "Авторизация через ВК отменена")

    if not state:
        logger.warning("VK callback: state missing from VK redirect")
        return _render_login(request, "Ошибка безопасности. Попробуйте снова.")

    code_verifier: str | None = None
    stored_state: str | None = None

    redis_pkce = pop_vk_pkce(state)
    if redis_pkce:
        code_verifier = redis_pkce.get("code_verifier")
        stored_state = state
        logger.info("VK callback: loaded PKCE from Redis for state=%s", state[:12])
    else:
        logger.warning("VK callback: PKCE missing in Redis for state=%s", state[:12])
        cookie_pkce, cookie_error = _load_pkce_cookie(request)
        if not cookie_pkce:
            return _render_login(request, cookie_error)
        if cookie_pkce.get("v") is not None:
            logger.warning("VK callback: cookie fallback skipped for current cookie version, state=%s", state[:12])
            return _render_login(request, "Ошибка сессии. Попробуйте снова или очистите cookies.")
        logger.info("VK callback: using cookie PKCE fallback for legacy flow state=%s", state[:12])
        code_verifier = cookie_pkce["cv"]
        stored_state = cookie_pkce["st"]

    if not code_verifier or not stored_state:
        logger.warning("VK callback: PKCE payload incomplete for state=%s", state[:12])
        return _render_login(request, "Ошибка сессии. Попробуйте снова или очистите cookies.")

    if stored_state != state:
        logger.warning("VK callback: state mismatch stored=%r url=%r", stored_state[:20], state[:20])
        return _render_login(request, "Ошибка безопасности. Попробуйте снова.")

    if not device_id:
        return _render_login(request, "Ошибка авторизации ВК: нет device_id.")

    try:
        token_data = await exchange_code(code, code_verifier, device_id)
    except Exception as exc:
        logger.error("VK token exchange failed: %s", exc)
        return _render_login(request, "Ошибка авторизации ВК. Попробуйте позже.")

    access_token = token_data.get("access_token")
    vk_user_id = token_data.get("user_id")
    if not access_token or not vk_user_id:
        return _render_login(request, "ВК не вернул данные авторизации.")

    is_member = await check_group_membership(access_token, vk_user_id, settings.vk_group_id)

    # VK ID tokens may lack groups scope for closed communities — fall back to community token.
    if is_member is not True and settings.vk_community_token:
        fallback = await check_group_membership(
            settings.vk_community_token, vk_user_id, settings.vk_group_id
        )
        if fallback is True:
            is_member = True
            logger.info("vk_callback: user %s confirmed via community token fallback", vk_user_id)
        elif is_member is None and fallback is False:
            is_member = False

    if is_member is None:
        # VK API didn't give a definite answer (timeout/error) — don't treat this
        # as a confirmed non-membership, ask the user to retry instead.
        logger.warning("vk_callback: membership check inconclusive for user %s", vk_user_id)
        return templates.TemplateResponse("denied.html", {
            "request": request,
            "reason": "Не удалось проверить участие в сообществе ВК. Попробуйте войти ещё раз через минуту.",
            "vk_group_id": settings.vk_group_id,
        })

    if not is_member:
        existing_user = db.query(User).filter(User.vk_id == vk_user_id).first()
        if existing_user:
            existing_user.is_group_member = False
            existing_user.last_vk_check_at = _now()
            db.commit()
        return templates.TemplateResponse("denied.html", {
            "request": request,
            "reason": "Доступ запрещён. Вы не являетесь участником сообщества.",
            "vk_group_id": settings.vk_group_id,
        })

    try:
        vk_info = await get_user_info(access_token, vk_user_id)
    except Exception as exc:
        logger.error("VK get_user_info failed: %s", exc)
        return _render_login(request, "Не удалось получить данные профиля ВК.")

    user = _upsert_user(
        db,
        vk_id=vk_user_id,
        name=vk_info["name"],
        first_name=vk_info.get("first_name"),
        last_name=vk_info.get("last_name"),
        photo_url=vk_info.get("photo_url"),
        is_group_member=True,
        mark_vk_checked=True,
    )

    if not user.is_active:
        db.commit()
        return templates.TemplateResponse("blocked.html", {"request": request})

    background.add_task(
        drive_service.sync_drive_works,
        user.id, user.vk_id, user.tariff or "", user.tg_username or "",
    )
    return _create_session_response(db, user)


@router.get("/auth/link", response_class=HTMLResponse)
async def one_time_link_login(
    request: Request,
    background: BackgroundTasks,
    db: Annotated[DBSession, Depends(get_db)],
    token: str | None = None,
):
    if not token:
        return _render_login(request, "Ссылка входа повреждена или неполная.")

    _, user, consume_error = consume_one_time_login_token(db, raw_token=token)
    if consume_error == "invalid":
        return _render_login(request, "Ссылка входа недействительна.")
    if consume_error == "expired":
        return _render_login(request, "Ссылка входа истекла. Запросите новую.")
    if consume_error in {"used", "revoked"}:
        return _render_login(request, "Эта ссылка уже использована. Запросите новую.")
    if not user:
        return _render_login(request, "Не удалось определить пользователя по ссылке.")

    if not user.is_active:
        return templates.TemplateResponse("denied.html", {
            "request": request,
            "reason": "Ваш доступ временно отключен. Напишите администратору.",
        })
    # Allow: VK group members, legacy admins, and staff (role rank >= 2)
    role_rank = user.role.rank if user.role else 0
    if not user.is_admin and not user.is_group_member and role_rank < 2:
        return templates.TemplateResponse("denied.html", {
            "request": request,
            "reason": "Доступ к кабинету доступен только участникам закрытой группы ВК.",
        })

    background.add_task(
        drive_service.sync_drive_works,
        user.id, user.vk_id, user.tariff or "", user.tg_username or "",
    )
    return _create_session_response(db, user)


@router.get("/auth/admin-access")
@limiter.limit("10/minute")
async def admin_permanent_access(
    request: Request,
    db: Annotated[DBSession, Depends(get_db)],
    key: str | None = None,
):
    """Permanent superadmin login via static secret key from .env."""
    if not key or not settings.admin_access_token or not secrets.compare_digest(key, settings.admin_access_token):
        raise HTTPException(status_code=404)

    user = db.query(User).filter(User.staff_login == settings.admin_staff_login).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=404)

    return _create_session_response(db, user)


@router.post("/auth/internal/issue-link")
def issue_one_time_link_internal(
    request: Request,
    payload: InternalIssueLinkRequest,
    db: Annotated[DBSession, Depends(get_db)],
    _: Annotated[None, Depends(require_internal_api_token)],
):
    user = _upsert_user(
        db,
        vk_id=payload.vk_id,
        name=payload.name,
        photo_url=payload.photo_url,
        tariff=payload.tariff,
        is_group_member=payload.is_group_member,
        mark_vk_checked=True,
    )

    if not payload.is_group_member:
        db.commit()
        return {
            "ok": False,
            "issued": False,
            "reason": "not_group_member",
            "message": "Пользователь не состоит в закрытой группе ВК.",
        }

    if not user.is_active:
        db.commit()
        return {
            "ok": False,
            "issued": False,
            "reason": "user_inactive",
            "message": "Пользователь отключен администратором.",
        }

    login_url, login_token = issue_one_time_login_link(
        db,
        user=user,
        base_url=_public_base_url(request),
        issued_by="n8n",
    )
    return {
        "ok": True,
        "issued": True,
        "login_url": login_url,
        "expires_at": login_token.expires_at.isoformat(),
        "vk_id": user.vk_id,
        "user_id": user.id,
    }


# ── GET /auth/handoff ─────────────────────────────────────────────────────────

@router.get("/auth/handoff")
def auth_handoff(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Свежая одноразовая ссылка входа для УЖЕ залогиненного пользователя —
    чтобы in-app браузер Telegram «передал» сессию во внешний браузер.

    Используется escape-скриптом в base.html: внутри Telegram пользователь уже
    авторизован (одноразовый токен из ссылки куратора тут уже потреблён), мы
    выпускаем новый короткоживущий токен, и внешний браузер, открыв эту ссылку,
    оказывается залогинен. Ответ — same-origin JSON; токен покидает устройство
    только через собственный браузерный «прыжок» пользователя.
    """
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    if not db_user or not db_user.is_active:
        return JSONResponse({"ok": False}, status_code=403)
    login_url, _token = issue_one_time_login_link(
        db,
        user=db_user,
        base_url=_public_base_url(request),
        issued_by="handoff",
    )
    return JSONResponse({"ok": True, "login_url": login_url})


# ── 3D Лаборатория ──────────────────────────────────────────────────────────


@router.get("/3dlab", response_class=HTMLResponse)
def lab3d_page(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Serve the 3D Lab app for VK group members, students, and staff."""
    if not user.get("is_group_member") and not user.get("is_admin") and user.get("role_rank", 0) < 1:
        return RedirectResponse("/denied", status_code=302)
    return templates.TemplateResponse("3dlab.html", {
        "request": request,
        "lab_user": {"id": user["vk_id"], "name": user["name"]},
    })


@router.get("/cabinet/3dlab/enter")
def enter_3dlab(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Issue a short-lived SSO token and redirect the user to 3D Lab."""
    if not user.get("is_group_member") and not user.get("is_admin") and user.get("role_rank", 0) < 1:
        return RedirectResponse("/denied", status_code=302)

    if not settings.lab3d_url:
        raise HTTPException(status_code=503, detail="3D Lab не настроена")

    lab3d_url = settings.lab3d_url.rstrip("/")

    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    raw_token, _ = issue_sso_token(
        db,
        user=db_user,
        ttl_minutes=settings.sso_token_ttl_minutes,
    )
    redirect_url = f"{lab3d_url}/auth/sso?token={raw_token}"
    return RedirectResponse(redirect_url, status_code=302)


@router.get("/denied", response_class=HTMLResponse)
def denied_page(request: Request):
    """Show the VK group access denial page used by redirects."""
    return templates.TemplateResponse("denied.html", {
        "request": request,
        "reason": "Доступ к 3D лаборатории открыт только участникам сообщества ВКонтакте.",
        "vk_group_id": settings.vk_group_id,
    }, status_code=403)


class SSOVerifyRequest(BaseModel):
    token: str


@router.post("/auth/internal/sso/verify")
def sso_verify(
    payload: SSOVerifyRequest,
    db: Annotated[DBSession, Depends(get_db)],
    _: Annotated[None, Depends(require_lab3d_token)],
):
    """Verify a cross-service SSO token. Called server-side by 3D Lab."""
    login_token, user, error = consume_one_time_login_token(db, raw_token=payload.token)

    if error:
        return JSONResponse(status_code=400, content={"ok": False, "reason": error})

    if not user.is_group_member:
        return JSONResponse(status_code=403, content={"ok": False, "reason": "not_group_member"})

    if not user.is_active:
        return JSONResponse(status_code=403, content={"ok": False, "reason": "user_inactive"})

    return {
        "ok": True,
        "user_id": user.id,
        "vk_id": user.vk_id,
        "name": user.name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "photo_url": user.photo_url,
        "is_group_member": user.is_group_member,
        "tariff": user.tariff,
        "expires_at": login_token.expires_at.isoformat(),
    }


@router.get("/auth/vk/recheck", response_class=HTMLResponse)
async def vk_recheck(
    request: Request,
    db: Annotated[DBSession, Depends(get_db)],
):
    """Re-check VK group membership without a full OAuth round-trip.

    Requires an active session. Used from the denied.html page.
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        return RedirectResponse("/auth/vk/login", status_code=302)

    row = (
        db.query(Session, User)
        .join(User, Session.user_id == User.id)
        .filter(Session.id == session_id, Session.is_active == True)
        .first()
    )
    if not row:
        return RedirectResponse("/auth/vk/login", status_code=302)

    _, user = row
    # We don't have the access_token anymore — use community token if available,
    # otherwise fall back to the VK API with service token approach.
    # For now: call groups.isMember with user's vk_id using community token if set.
    from app.services.vk import _vk_api_get
    community_token = settings.vk_community_token
    if community_token:
        try:
            data = await _vk_api_get("groups.isMember", {
                "group_id": settings.vk_group_id,
                "user_id": user.vk_id,
                "access_token": community_token,
                "v": "5.199",
            })
            is_member = data.get("response") == 1
        except Exception as exc:
            logger.warning("recheck groups.isMember failed: %s", exc)
            is_member = False
    else:
        # No token available — redirect to full OAuth
        return RedirectResponse("/auth/vk/login", status_code=302)

    user.is_group_member = is_member
    user.last_vk_check_at = _now()
    db.commit()

    if is_member:
        return RedirectResponse("/cabinet", status_code=302)

    return templates.TemplateResponse("denied.html", {
        "request": request,
        "reason": "Вы всё ещё не являетесь участником сообщества. Вступите и попробуйте снова.",
        "vk_group_id": settings.vk_group_id,
    })


def _render_staff_login(request: Request, error: str | None = None):
    return templates.TemplateResponse("staff_login.html", {
        "request": request,
        "error": error,
    })


@router.get("/login", response_class=HTMLResponse)
def staff_login_form(request: Request):
    return _render_staff_login(request)


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
def staff_login_submit(
    request: Request,
    db: Annotated[DBSession, Depends(get_db)],
    login: str = Form(...),
    password: str = Form(...),
):
    login_clean = login.strip().lower()
    user = db.query(User).filter(
        func.lower(User.staff_login) == login_clean,
    ).first()

    if not user or not user.password_hash:
        return _render_staff_login(request, "Неверный логин или пароль")

    try:
        pw_ok = _bcrypt_lib.checkpw(password.encode(), user.password_hash.encode())
    except ValueError:
        # password_hash is not a valid bcrypt hash — treat as wrong password
        pw_ok = False
    if not pw_ok:
        return _render_staff_login(request, "Неверный логин или пароль")

    if not user.is_active:
        return _render_staff_login(request, "Аккаунт отключён. Обратитесь к администратору.")

    role_rank = user.role.rank if user.role else 0
    if role_rank < 1:
        return _render_staff_login(request, "Для этого аккаунта не назначена роль.")

    return _create_session_response(db, user)


# Обратная совместимость — старая ссылка перенаправляет на /login
@router.get("/auth/staff/login")
def staff_login_redirect():
    return RedirectResponse("/login", status_code=301)


@router.post("/logout")
def logout(
    request: Request,
    db: Annotated[DBSession, Depends(get_db)],
):
    session_id = request.cookies.get("session_id")
    if session_id:
        invalidate_session(session_id)
        session = db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.is_active = False
            db.commit()

    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session_id")
    return response
