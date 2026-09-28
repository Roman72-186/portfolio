from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.constants import FEATURE_MOCK_EXAM
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf
from app.services.tz import msk_midnight
from app.services.feature_periods import get_active_period
from app.services.notify import notify
from app.services.point_a import maybe_notify_point_a_level
from app.services.staff_dashboard import (
    build_tariff_registration_csv,
    get_tariff_registration_stats,
    load_staff_dashboard,
    parse_registration_date,
)
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


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/admin-panel", response_class=HTMLResponse)
def cabinet_admin(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    ctx = load_staff_dashboard(db, user, datetime.now(timezone.utc))
    ctx.update({"request": request, "user": user})
    return templates.TemplateResponse(request, "cabinet_staff.html", ctx)


@router.get("/admin/registration-stats.csv")
def download_admin_registration_stats(
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    registration_from: str | None = Query(None),
    registration_to: str | None = Query(None),
    registration_tariff: str = Query(""),
):
    stats = get_tariff_registration_stats(db, period_from=parse_registration_date(registration_from), period_to=parse_registration_date(registration_to), tariff_filter=registration_tariff)
    return Response(
        content=build_tariff_registration_csv(stats),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=registration-stats.csv"},
    )


# ── Mock exam check (dedicated split-panel) ──────────────────────────────────

@router.get("/admin/mock-check", response_class=HTMLResponse)
def admin_mock_check(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    student: int = Query(0),
):
    from app.constants import MOCK_SUBJECTS, TARIFFS
    from app.models.role import Role

    student_role = db.query(Role).filter(Role.rank == 1).first()
    sidebar_students: list[dict] = []
    total_unchecked = 0
    if student_role:
        active_period = get_active_period(db, FEATURE_MOCK_EXAM)

        mock_q = (
            db.query(Work.user_id, Work.score, Work.subject)
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
        scored_subjects_by_user: dict[int, set] = defaultdict(set)
        for row in mock_rows:
            counts_by_user[row.user_id] += 1
            if row.score is None:
                unchecked_by_user[row.user_id] += 1
            elif row.subject:
                scored_subjects_by_user[row.user_id].add(row.subject)

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
                    "scored_subjects": list(scored_subjects_by_user.get(s.id, set())),
                })
            sidebar_students.sort(key=lambda x: (-x["unchecked"], x["name"]))
            total_unchecked = sum(unchecked_by_user.values())

    tariffs_present = sorted({s["tariff"] for s in sidebar_students if s["tariff"]})

    return templates.TemplateResponse(request, "cabinet_admin_mock_check.html", {
        "request": request,
        "user": user,
        "sidebar_students": sidebar_students,
        "initial_student_id": student,
        "mock_subjects": MOCK_SUBJECTS,
        "total_unchecked": total_unchecked,
        "total_students": len(sidebar_students),
        "tariffs": tariffs_present,
    })


# ── Retake check (dedicated split-panel) ─────────────────────────────────────

@router.get("/admin/retake-check", response_class=HTMLResponse)
def admin_retake_check(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    student: int = Query(0),
):
    from app.models.role import Role

    student_role = db.query(Role).filter(Role.rank == 1).first()
    sidebar_students: list[dict] = []
    total_unchecked = 0
    if student_role:
        retake_rows = (
            db.query(Work.user_id, Work.score)
            .filter(
                Work.work_type == WORK_TYPE_RETAKE,
                Work.status == "success",
            )
            .all()
        )
        counts_by_user: dict[int, int] = defaultdict(int)
        unchecked_by_user: dict[int, int] = defaultdict(int)
        for row in retake_rows:
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
                    "tg_username": s.tg_username,
                    "photo_url": s.photo_url,
                    "tariff": s.tariff,
                    "retake_count": counts_by_user.get(s.id, 0),
                    "unchecked": unchecked_by_user.get(s.id, 0),
                })
            sidebar_students.sort(key=lambda x: (-x["unchecked"], x["name"]))
            total_unchecked = sum(unchecked_by_user.values())

    tariffs_present = sorted({s["tariff"] for s in sidebar_students if s["tariff"]})

    return templates.TemplateResponse(request, "cabinet_admin_retake_check.html", {
        "request": request,
        "user": user,
        "is_superadmin": user.get("role_rank", 0) >= 5,
        "sidebar_students": sidebar_students,
        "initial_student_id": student,
        "total_unchecked": total_unchecked,
        "total_students": len(sidebar_students),
        "tariffs": tariffs_present,
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
    background_tasks: BackgroundTasks,
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

    notification = Notification(
        user_id=work.user_id,
        title=f"Работа проверена — {int(work.score)} / 100",
        text=work.comment if work.comment else None,
        work_id=work.id,
    )
    db.add(notification)
    student = db.get(User, work.user_id)
    point_a_notification = maybe_notify_point_a_level(db, student) if student else None
    db.commit()
    invalidate_unread(work.user_id)
    background_tasks.add_task(notify, notification.id)
    if point_a_notification is not None:
        background_tasks.add_task(notify, point_a_notification.id)

    dest = redirect_to or f"/cabinet/students?student={work.user_id}&tab=mock-exams"
    return RedirectResponse(dest, status_code=302)
