import logging
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import bcrypt as _bcrypt_lib
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.constants import TARIFFS, TARIFF_DISPLAY
from app.db.database import get_db
from app.dependencies import require_admin, require_csrf
from app.models.user import User
from app.models.role import Role
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM
from app.services import s3 as s3_service
from app.services.auth_links import issue_one_time_login_link
from app.services.user_management import toggle_user_active
from app.tmpl import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _hash_password(plain: str) -> str:
    return _bcrypt_lib.hashpw(plain.encode(), _bcrypt_lib.gensalt()).decode()


# Human-readable tariff labels for admin dropdowns
TARIFF_LABELS = list(TARIFF_DISPLAY.values())

# Characters for auto-generated logins/passwords — no visually confusing chars
_CRED_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


def _gen_token(length: int = 8) -> str:
    return "".join(secrets.choice(_CRED_CHARS) for _ in range(length))


def _get_user_by_id(db: DBSession, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def _render_admin_users(
    request: Request,
    user: dict,
    db: DBSession,
    *,
    issued_link_user_id: int | None = None,
    issued_link: str | None = None,
    issued_link_expires_at=None,
    page_error: str | None = None,
    new_staff_creds: dict | None = None,
):
    # Limit: max 1000 recent users (защита от медленной выборки при большом кол-ве юзеров)
    users = db.query(User).order_by(User.created_at.desc()).limit(1000).all()
    roles = db.query(Role).order_by(Role.rank).all()
    curator_role = db.query(Role).filter(Role.name == "куратор").first()
    # Limit: max 200 curators
    curators = (
        db.query(User).filter(User.role_id == curator_role.id, User.is_active == True).limit(200).all()
        if curator_role else []
    )
    return templates.TemplateResponse("admin_users.html", {
        "request": request,
        "user": user,
        "users": users,
        "tariffs": TARIFF_LABELS,
        "tariff_display": TARIFF_DISPLAY,
        "roles": roles,
        "curators": curators,
        "current_user_rank": user.get("role_rank", 5),
        "issued_link_user_id": issued_link_user_id,
        "issued_link": issued_link,
        "issued_link_expires_at": issued_link_expires_at,
        "page_error": page_error,
        "new_staff_creds": new_staff_creds,
    })


@router.get("/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
):
    return _render_admin_users(request, user, db)


def _build_migration_path(work: Work, vk_id: int, new_tariff: str) -> str | None:
    """Build new S3 path for a work when its student's tariff changes."""
    src = work.s3_path or ""
    current_name = src.rsplit("/", 1)[-1] if src else (work.filename or "photo.jpg")
    new_name = s3_service._make_filename(new_tariff, vk_id, current_name)
    tf = s3_service.tariff_display(new_tariff)

    if work.work_type == WORK_TYPE_BEFORE:
        return f"Портфолио/{tf}/{tf}_{vk_id}/До/{new_name}"
    if work.work_type == WORK_TYPE_AFTER:
        return f"Портфолио/{tf}/{tf}_{vk_id}/После/{new_name}"
    if work.work_type == WORK_TYPE_MOCK_EXAM:
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
        for part in src.split("/"):
            if len(part) == 7 and part[4:5] == "-":
                ym = part
                break
        return f"Пробники/{tf}/{tf}_{vk_id}/{ym}/{new_name}"
    return None


@router.post("/users/{user_id}/tariff")
def update_tariff(
    user_id: int,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    tariff: str = Form(...),
):
    # Normalize to canonical UPPER form
    tariff = tariff.strip().upper()
    if tariff not in TARIFFS:
        return RedirectResponse("/admin/users", status_code=302)
    target_user = _get_user_by_id(db, user_id)
    if not target_user or target_user.tariff == tariff:
        return RedirectResponse("/admin/users", status_code=302)

    if s3_service.is_configured():
        # Limit: max 500 works per user (защита при смене тарифа с большим кол-вом работ)
        works = db.query(Work).filter(
            Work.user_id == user_id,
            Work.s3_path.isnot(None),
        ).limit(500).all()
        for work in works:
            new_path = _build_migration_path(work, target_user.vk_id, tariff)
            if new_path:
                ok = s3_service.move_s3_object(work.s3_path, new_path)
                if ok:
                    work.s3_path = new_path
                    work.s3_url = s3_service.s3_public_url(new_path)
                else:
                    logger.warning(
                        "S3 move failed for work_id=%s (user_id=%s, %s→%s)",
                        work.id, user_id, target_user.tariff, tariff,
                    )
        db.flush()

    target_user.tariff = tariff
    db.query(Work).filter(Work.user_id == user_id).update({"tariff": tariff})
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/role")
def assign_role(
    user_id: int,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    role_id: str = Form(...),
):
    acting_rank = user.get("role_rank", 0)
    target_user = _get_user_by_id(db, user_id)
    if not target_user:
        return RedirectResponse("/admin/users", status_code=302)

    # Cannot modify yourself
    if target_user.id == user["user_id"]:
        return RedirectResponse("/admin/users", status_code=302)

    # Cannot modify users whose current rank >= your own rank
    target_current_rank = target_user.role.rank if target_user.role else 0
    if target_current_rank >= acting_rank:
        return RedirectResponse("/admin/users", status_code=302)

    if role_id == "":
        target_user.role_id = None
        db.commit()
        return RedirectResponse("/admin/users", status_code=302)

    new_role = db.query(Role).filter(Role.id == int(role_id)).first()
    if not new_role:
        return RedirectResponse("/admin/users", status_code=302)

    # Cannot assign a role equal to or higher than your own rank
    if new_role.rank >= acting_rank:
        return RedirectResponse("/admin/users", status_code=302)

    target_user.role_id = new_role.id
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/issue-link", response_class=HTMLResponse)
def issue_link(
    user_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    target_user = _get_user_by_id(db, user_id)
    if not target_user:
        return _render_admin_users(request, user, db, page_error="Пользователь не найден.")
    if not target_user.is_active:
        return _render_admin_users(
            request, user, db,
            page_error="Нельзя выпустить ссылку для неактивного пользователя.",
        )
    if not target_user.role_id and not target_user.is_group_member and not target_user.is_admin:
        return _render_admin_users(
            request, user, db,
            page_error="Одноразовая ссылка доступна только пользователям с назначенной ролью.",
        )

    issued_link, login_token = issue_one_time_login_link(
        db,
        user=target_user,
        base_url=f"https://{settings.domain}" if settings.domain else str(request.base_url).rstrip("/"),
        issued_by=f"admin:{user['user_id']}",
    )
    return _render_admin_users(
        request, user, db,
        issued_link_user_id=target_user.id,
        issued_link=issued_link,
        issued_link_expires_at=login_token.expires_at,
    )


@router.post("/users/{user_id}/toggle-active")
def toggle_active(
    user_id: int,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    toggle_user_active(db, target_user_id=user_id, performed_by_id=user["user_id"])
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/toggle-admin")
def toggle_admin(
    user_id: int,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    target_user = _get_user_by_id(db, user_id)
    if target_user and target_user.id != user["user_id"]:
        target_user.is_admin = not target_user.is_admin
        db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/set-password")
def reset_staff_credentials(
    user_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Auto-generate new login (if absent) and password for an existing staff account."""
    target_user = _get_user_by_id(db, user_id)
    if not target_user:
        return RedirectResponse("/admin/users", status_code=302)

    role_rank = target_user.role.rank if target_user.role else 0
    if role_rank < 2 and not target_user.is_admin:
        return _render_admin_users(request, user, db, page_error="Сбросить данные можно только сотрудникам (ранг ≥ 2).")

    new_password = _gen_token(8)
    if not target_user.staff_login:
        for _ in range(10):
            candidate = _gen_token(8)
            if not db.query(User).filter(
                func.lower(User.staff_login) == candidate,
                User.id != user_id,
            ).first():
                target_user.staff_login = candidate
                break

    target_user.password_hash = _hash_password(new_password)
    db.commit()

    return _render_admin_users(
        request, user, db,
        new_staff_creds={
            "name": target_user.name,
            "login": target_user.staff_login,
            "password": new_password,
        },
    )


@router.post("/staff/create")
def create_staff_account(
    request: Request,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    first_name: str = Form(...),
    last_name: str = Form(""),
    role_id: int = Form(...),
):
    """Create a new staff account — login and password are auto-generated."""
    acting_rank = user.get("role_rank", 0)

    new_role = db.query(Role).filter(Role.id == role_id).first()
    if not new_role:
        return _render_admin_users(request, user, db, page_error="Роль не найдена.")
    if new_role.rank < 2:
        return _render_admin_users(request, user, db, page_error="Аккаунт сотрудника требует роль с рангом ≥ 2.")
    if new_role.rank >= acting_rank:
        return _render_admin_users(request, user, db, page_error="Нельзя создать аккаунт с рангом не ниже вашего.")

    login = _gen_token(8)
    for _ in range(10):
        if not db.query(User).filter(func.lower(User.staff_login) == login).first():
            break
        login = _gen_token(8)

    password = _gen_token(8)
    full_name = f"{first_name.strip()} {last_name.strip()}".strip()

    min_vk = db.query(func.min(User.vk_id)).scalar() or 0
    new_vk_id = min(min_vk - 1, -1)

    new_user = User(
        vk_id=new_vk_id,
        name=full_name,
        first_name=first_name.strip(),
        last_name=last_name.strip() or None,
        staff_login=login,
        password_hash=_hash_password(password),
        role_id=role_id,
        is_active=True,
        is_group_member=False,
        tariff="УВЕРЕННЫЙ",
    )
    db.add(new_user)
    db.commit()

    return _render_admin_users(
        request, user, db,
        new_staff_creds={"name": full_name, "login": login, "password": password},
    )


@router.post("/users/{user_id}/curator")
def assign_curator(
    user_id: int,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    curator_id: str = Form(...),
):
    """Assign or remove a curator for a student."""
    target_user = _get_user_by_id(db, user_id)
    if not target_user:
        return RedirectResponse("/admin/users", status_code=302)
    if curator_id:
        try:
            cid = int(curator_id)
        except ValueError:
            return RedirectResponse("/admin/users", status_code=302)
        curator = db.query(User).filter(User.id == cid).first()
        if not curator or (curator.role and curator.role.rank < 2):
            return RedirectResponse("/admin/users", status_code=302)
        target_user.curator_id = cid
    else:
        target_user.curator_id = None
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/works/{work_id}/score")
def set_work_score(
    work_id: int,
    user: Annotated[dict, Depends(require_admin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    score: float = Form(...),
    redirect_to: str = Form("/admin/users"),
):
    if not redirect_to.startswith("/") or redirect_to.startswith("//"):
        redirect_to = "/admin/users"
    work = db.query(Work).filter(Work.id == work_id).first()
    if work:
        work.score = max(0.0, min(100.0, score))
        work.scored_at = datetime.now(timezone.utc)
        work.scored_by_id = user["user_id"]
        db.commit()
    return RedirectResponse(redirect_to, status_code=302)
