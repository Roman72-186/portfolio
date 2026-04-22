from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession, aliased

from app.cache import invalidate_unread
from app.constants import FEATURE_PORTFOLIO_UPLOAD, FEATURE_MOCK_EXAM, FEATURE_RETAKE, FEATURE_LABELS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf
from app.models.feature_period import FeaturePeriod
from app.services.tz import today_msk, msk_midnight
from app.services.feature_periods import get_active_period
from app.models.notification import Notification
from app.models.role import Role
from app.models.user import User
from app.models.work import (
    Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER,
    WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE,
)
from app.services.utils import study_duration_text, group_works
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _month_name_prep(month: int) -> str:
    names = ["", "январе", "феврале", "марте", "апреле", "мае", "июне",
             "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]
    return names[month] if 1 <= month <= 12 else ""


def _load_dashboard_data(db: DBSession, now: datetime) -> dict:
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    role_rows = (
        db.query(Role.display_name, Role.rank, func.count(User.id).label("cnt"))
        .outerjoin(User, (User.role_id == Role.id) & (User.is_active == True))
        .group_by(Role.id, Role.display_name, Role.rank)
        .order_by(Role.rank)
        .all()
    )
    role_breakdown = [{"name": r.display_name, "rank": r.rank, "count": r.cnt} for r in role_rows]
    total_active = sum(r["count"] for r in role_breakdown)
    inactive_count = db.query(func.count(User.id)).filter(User.is_active == False).scalar() or 0
    new_users_month = (
        db.query(func.count(User.id)).filter(User.created_at >= month_start).scalar() or 0
    )

    works_type_rows = (
        db.query(Work.work_type, func.count(Work.id))
        .filter(Work.status == "success")
        .group_by(Work.work_type)
        .all()
    )
    works_by_type = {wt: cnt for wt, cnt in works_type_rows}
    total_works = sum(works_by_type.values())
    works_this_month = (
        db.query(func.count(Work.id))
        .filter(Work.status == "success", Work.created_at >= month_start)
        .scalar() or 0
    )

    avg_score_raw = (
        db.query(func.avg(Work.score))
        .filter(Work.score.isnot(None), Work.status == "success")
        .scalar()
    )
    avg_score = round(float(avg_score_raw)) if avg_score_raw is not None else None
    mock_period = get_active_period(db, FEATURE_MOCK_EXAM)
    if mock_period:
        _mock_start = msk_midnight(mock_period.start_date)
        _mock_end = msk_midnight(mock_period.end_date + timedelta(days=1))
        unscored_mocks = (
            db.query(func.count(Work.id))
            .filter(
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.score.is_(None),
                Work.status == "success",
                Work.created_at >= _mock_start,
                Work.created_at < _mock_end,
            )
            .scalar() or 0
        )
    else:
        unscored_mocks = 0

    # Curators list (read-only)
    StudentAlias = aliased(User)
    curator_rows = (
        db.query(
            User.id, User.first_name, User.last_name, User.name, User.photo_url,
            func.count(StudentAlias.id).label("student_count"),
        )
        .join(Role, User.role_id == Role.id)
        .outerjoin(StudentAlias, (StudentAlias.curator_id == User.id) & (StudentAlias.is_active == True))
        .filter(Role.rank == 2, User.is_active == True)
        .group_by(User.id, User.first_name, User.last_name, User.name, User.photo_url)
        .order_by(func.count(StudentAlias.id).desc())
        .limit(100)
        .all()
    )
    curators = [
        {
            "id": r.id,
            "name": f"{r.last_name or ''} {r.first_name or r.name}".strip(),
            "photo_url": r.photo_url,
            "student_count": r.student_count,
        }
        for r in curator_rows
    ]

    # Admins list (rank 4)
    admin_rows = (
        db.query(User.id, User.first_name, User.last_name, User.name, User.photo_url)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 4, User.is_active == True)
        .order_by(User.last_name, User.first_name)
        .limit(50)
        .all()
    )
    admins = [
        {
            "id": r.id,
            "name": f"{r.last_name or ''} {r.first_name or r.name}".strip(),
            "photo_url": r.photo_url,
        }
        for r in admin_rows
    ]

    # Recent uploads
    recent_rows = (
        db.query(Work, User)
        .join(User, Work.user_id == User.id)
        .filter(Work.status == "success")
        .order_by(Work.created_at.desc())
        .limit(10)
        .all()
    )
    recent_works = [
        {
            "work_type": w.work_type,
            "filename": w.filename,
            "created_at": w.created_at,
            "s3_url": w.s3_url,
            "student_name": f"{u.last_name or ''} {u.first_name or u.name}".strip(),
            "student_id": u.id,
            "score": float(w.score) if w.score is not None else None,
            "work_id": w.id,
        }
        for w, u in recent_rows
    ]

    today = today_msk()
    _features = [FEATURE_PORTFOLIO_UPLOAD, FEATURE_MOCK_EXAM, FEATURE_RETAKE]
    feature_statuses: dict = {}
    for _feat in _features:
        _period = (
            db.query(FeaturePeriod)
            .filter(
                FeaturePeriod.feature == _feat,
                FeaturePeriod.is_active.is_(True),
                FeaturePeriod.start_date <= today,
                FeaturePeriod.end_date >= today,
            )
            .first()
        )
        feature_statuses[_feat] = {
            "open": _period is not None,
            "label": FEATURE_LABELS.get(_feat, _feat),
            "period_id": _period.id if _period else None,
        }

    return {
        "total_active": total_active,
        "inactive_count": inactive_count,
        "new_users_month": new_users_month,
        "role_breakdown": role_breakdown,
        "total_works": total_works,
        "works_this_month": works_this_month,
        "works_by_type": works_by_type,
        "avg_score": avg_score,
        "unscored_mocks": unscored_mocks,
        "curators": curators,
        "admins": admins,
        "recent_works": recent_works,
        "month_name": _month_name_prep(now.month),
        "feature_statuses": feature_statuses,
    }


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/admin-panel", response_class=HTMLResponse)
def cabinet_admin(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    now = datetime.now(timezone.utc)
    ctx = _load_dashboard_data(db, now)
    ctx.update({"request": request, "user": user})
    return templates.TemplateResponse("cabinet_staff.html", ctx)


# ── Mock exam check (dedicated split-panel) ──────────────────────────────────

@router.get("/admin/mock-check", response_class=HTMLResponse)
def admin_mock_check(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    student: int = Query(0),
):
    from app.constants import MOCK_SUBJECTS
    from app.models.role import Role

    student_role = db.query(Role).filter(Role.rank == 1).first()
    sidebar_students: list[dict] = []
    if student_role:
        active_period = get_active_period(db, FEATURE_MOCK_EXAM)

        mock_q = (
            db.query(Work.user_id, Work.score)
            .filter(
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.status == "success",
            )
        )
        if active_period:
            _mp_start = msk_midnight(active_period.start_date)
            _mp_end = msk_midnight(active_period.end_date + timedelta(days=1))
            mock_q = mock_q.filter(
                Work.created_at >= _mp_start,
                Work.created_at < _mp_end,
            )

        mock_rows = mock_q.all()
        counts_by_user: dict[int, int] = defaultdict(int)
        unchecked_by_user: dict[int, int] = defaultdict(int)
        for row in mock_rows:
            counts_by_user[row.user_id] += 1
            if row.score is None:
                unchecked_by_user[row.user_id] += 1

        if counts_by_user:
            users = (
                db.query(User)
                .filter(
                    User.id.in_(counts_by_user.keys()),
                    User.is_active == True,
                )
                .order_by(User.last_name, User.first_name)
                .all()
            )
            for s in users:
                sidebar_students.append({
                    "id": s.id,
                    "name": f"{s.last_name or ''} {s.first_name or s.name}".strip(),
                    "photo_url": s.photo_url,
                    "tariff": s.tariff,
                    "mock_count": counts_by_user.get(s.id, 0),
                    "unchecked": unchecked_by_user.get(s.id, 0),
                })
            sidebar_students.sort(key=lambda x: (-x["unchecked"], x["name"]))

    return templates.TemplateResponse("cabinet_admin_mock_check.html", {
        "request": request,
        "user": user,
        "sidebar_students": sidebar_students,
        "initial_student_id": student,
        "mock_subjects": MOCK_SUBJECTS,
    })


# ── Students list ─────────────────────────────────────────────────────────────

@router.get("/admin/students", response_class=HTMLResponse)
def admin_students_list(_user: Annotated[dict, Depends(require_admin_role)]):
    """Перенаправляет на единый кабинет учеников."""
    return RedirectResponse("/cabinet/students", status_code=302)


# ── Student works (split-panel for scoring) ──────────────────────────────────

@router.get("/admin/students/{student_id}/works", response_class=HTMLResponse)
def admin_student_works(student_id: int, _user: Annotated[dict, Depends(require_admin_role)]):
    """Перенаправляет на единый кабинет учеников (карточка ученика)."""
    return RedirectResponse(f"/cabinet/students?student={student_id}&tab=mock-exams", status_code=302)


# ── POST: score work (admin) ─────────────────────────────────────────────────

@router.post("/admin/works/{work_id}/score")
def admin_score_work(
    work_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    score: float = Form(...),
    comment: str = Form(""),
    redirect_to: str = Form(""),
):
    work = db.query(Work).filter(Work.id == work_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")

    if redirect_to and (not redirect_to.startswith("/") or redirect_to.startswith("//")):
        redirect_to = ""

    work.score = max(0, min(100, int(round(score))))
    work.comment = comment.strip() or None
    work.scored_at = datetime.now(timezone.utc)
    work.scored_by_id = user["user_id"]

    db.add(Notification(
        user_id=work.user_id,
        title=f"Работа проверена — {int(work.score)} / 100",
        text=work.comment if work.comment else None,
        work_id=work.id,
    ))
    db.commit()
    invalidate_unread(work.user_id)

    dest = redirect_to or f"/cabinet/students?student={work.user_id}&tab=mock-exams"
    return RedirectResponse(dest, status_code=302)
