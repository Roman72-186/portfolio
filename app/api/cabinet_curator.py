from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.constants import MOCK_SUBJECTS, TARIFFS, TARIFF_DISPLAY
from app.db.database import get_db
from app.dependencies import get_current_user, require_curator, require_admin_role, require_csrf
from app.models.curator_report import CuratorReport
from app.models.mock_exam_lock import MockExamLock
from app.models.notification import Notification
from app.models.role import Role
from app.models.user import User
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services import feedback as fb_service
from app.services import s3 as s3_service
from app.services.student_access import get_student_for_staff_access
from app.services.tz import MSK_TZ
from app.services.utils import study_duration_text, group_works
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")

PAGE_SIZE = 10
TARIFF_LABELS = list(TARIFF_DISPLAY.values())
# Видео-параметры общие с диалогом ОС — единый источник в services/feedback.
MAX_CURATOR_REPORT_VIDEO_SIZE = fb_service.MAX_FEEDBACK_VIDEO_SIZE
ALLOWED_CURATOR_REPORT_VIDEO_TYPES = fb_service.ALLOWED_FEEDBACK_VIDEO_TYPES
ALLOWED_CURATOR_REPORT_VIDEO_EXTENSIONS = fb_service.ALLOWED_FEEDBACK_VIDEO_EXTENSIONS


def _report_redirect_err(message: str) -> RedirectResponse:
    return RedirectResponse(
        f"/cabinet/curator/reports?err={quote_plus(message)}",
        status_code=302,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _current_academic_period() -> tuple[str, str]:
    """Возвращает (period_start, period_end) для текущего учебного года (сент – май)."""
    now = datetime.now(timezone.utc)
    year_start = now.year if now.month >= 9 else now.year - 1
    return (
        datetime(year_start, 9, 1).strftime("%d.%m.%Y"),
        datetime(year_start + 1, 5, 31).strftime("%d.%m.%Y"),
    )


def _get_curator_students(curator_id: int, db: DBSession) -> list[User]:
    return (
        db.query(User)
        .filter(
            User.curator_id == curator_id,
            User.is_active == True,
            User.course_periods.isnot(None),
            User.lessons_count.isnot(None),
        )
        .order_by(User.created_at.desc())
        .all()
    )


def _batch_load_works(student_ids: list[int], db: DBSession) -> dict[int, list]:
    works_by_user: dict[int, list] = defaultdict(list)
    if student_ids:
        # Limit: max 10000 works для batch-загрузки (защита при большом кол-ве студентов/работ)
        all_works = (
            db.query(Work)
            .filter(Work.user_id.in_(student_ids), Work.status == "success")
            .order_by(Work.created_at.desc())
            .limit(10000)
            .all()
        )
        for w in all_works:
            works_by_user[w.user_id].append(w)
    return works_by_user


def _enrich_for_sidebar(s: User, works_by_user: dict) -> dict:
    enrolled_at = s.enrolled_at or s.created_at
    works = works_by_user.get(s.id, [])
    mock_works = [w for w in works if w.work_type == WORK_TYPE_MOCK_EXAM]
    scored = [w for w in mock_works if w.score is not None]
    avg_score = (
        round(sum(float(w.score) for w in scored) / len(scored))
        if scored else None
    )
    return {
        "id": s.id,
        "name": f"{s.last_name or ''} {s.first_name or s.name}".strip(),
        "photo_url": s.photo_url,
        "tariff": s.tariff,
        "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
        "avg_score": avg_score,
        "upload_count": len(works),
        "portfolio_do_completed": s.portfolio_do_completed,
    }


def _check_student_access(student_id: int, user: dict, db: DBSession) -> User:
    return get_student_for_staff_access(
        db,
        user,
        student_id,
        not_found_detail="Ученик не найден",
        forbidden_detail="Нет доступа к этому ученику",
    )


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/curator", response_class=HTMLResponse)
def cabinet_curator_dashboard(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
):
    """Дашборд куратора: синяя плашка с именем + блоки навигации по разделам."""
    return templates.TemplateResponse("cabinet_curator_dashboard.html", {
        "request": request,
        "user": user,
        "nav_active": "dashboard",
    })


# ── Reports (видео-отчёты куратора) ───────────────────────────────────────────

@router.get("/curator/reports", response_class=HTMLResponse)
def curator_reports(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    ok: int = 0,
    deleted: int = 0,
    err: str = "",
):
    rank = user["role_rank"]
    # Куратор — своя страница (форма + своя история); админ/SA — список всех отчётов.
    if not (rank == 2 or rank >= 4):
        raise HTTPException(status_code=403, detail="Нет доступа")
    is_staff = rank >= 4

    q = db.query(CuratorReport)
    if not is_staff:
        q = q.filter(CuratorReport.curator_id == user["user_id"])
    reports = q.order_by(CuratorReport.created_at.desc()).limit(100 if is_staff else 50).all()

    # Первый просмотр staff'ом фиксируем для статистики «время до просмотра отчёта»
    if is_staff:
        unseen_ids = [r.id for r in reports if r.viewed_at is None]
        if unseen_ids:
            db.query(CuratorReport).filter(CuratorReport.id.in_(unseen_ids)).update(
                {"viewed_at": datetime.now(timezone.utc), "viewed_by_id": user["user_id"]},
                synchronize_session=False,
            )
            db.commit()

    # Имена кураторов нужны только для staff-вида
    curator_names: dict[int, str] = {}
    if is_staff and reports:
        ids = {r.curator_id for r in reports}
        for u in db.query(User).filter(User.id.in_(ids)).all():
            curator_names[u.id] = f"{u.last_name or ''} {u.first_name or u.name}".strip()

    history = []
    for r in reports:
        created = r.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        local_dt = created.astimezone(MSK_TZ) if created else None
        history.append({
            "id": r.id,
            "video_url": r.video_url,
            "text": r.text,
            "date_label": local_dt.strftime("%d.%m.%Y %H:%M") if local_dt else "",
            "curator_name": curator_names.get(r.curator_id) if is_staff else None,
        })
    return templates.TemplateResponse("cabinet_curator_reports.html", {
        "request": request,
        "user": user,
        "nav_active": "reports",
        "can_submit": rank == 2,
        "is_staff": is_staff,
        "history": history,
        "ok": bool(ok),
        "deleted": bool(deleted),
        "err": err,
    })


@router.post("/curator/reports")
async def curator_reports_submit(
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    video: UploadFile = File(...),
    text: str = Form(""),
):
    filename = video.filename or ""
    ext = Path(filename).suffix.lower()
    content_type = (video.content_type or "").lower()

    if content_type not in ALLOWED_CURATOR_REPORT_VIDEO_TYPES and ext not in ALLOWED_CURATOR_REPORT_VIDEO_EXTENSIONS:
        return _report_redirect_err("Загрузите видео-файл в формате mp4, mov, webm, avi, mkv, wmv или 3gp.")

    video_bytes = await video.read()
    if not video_bytes:
        return _report_redirect_err("Видео-файл пустой. Выберите или снимите видео заново.")
    if len(video_bytes) > MAX_CURATOR_REPORT_VIDEO_SIZE:
        return _report_redirect_err("Видео слишком большое. Максимальный размер файла - 500 МБ.")

    s3_path = s3_service.s3_path_curator_report(user["user_id"], filename)
    video_url = s3_service.upload_to_s3(s3_path, video_bytes, content_type or "video/mp4")
    if s3_service.is_configured() and not video_url:
        return _report_redirect_err("Не удалось загрузить видео. Попробуйте ещё раз.")
    if not video_url:
        return _report_redirect_err("Хранилище видео не настроено. Обратитесь к администратору.")

    text = text.strip()[:2000] or None

    report = CuratorReport(
        curator_id=user["user_id"],
        video_url=video_url,
        text=text,
    )
    db.add(report)

    curator_name = user.get("name") or user.get("first_name") or "куратор"
    notif_text = "\n".join(filter(None, [text, video_url]))

    admins = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank >= 4, User.is_active == True)
        .all()
    )
    for admin in admins:
        db.add(Notification(
            user_id=admin.id,
            title=f"Видео-отчёт от куратора {curator_name}",
            text=notif_text,
            work_id=None,
        ))

    db.commit()
    for admin in admins:
        invalidate_unread(admin.id)

    return RedirectResponse("/cabinet/curator/reports?ok=1", status_code=302)


@router.post("/curator/reports/{report_id}/delete")
def curator_reports_delete(
    report_id: int,
    _user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    report = db.query(CuratorReport).filter(CuratorReport.id == report_id).first()
    if not report:
        return _report_redirect_err("Видео-отчёт не найден")

    s3_path = s3_service.s3_path_from_public_url(report.video_url)
    if s3_path:
        s3_service.delete_from_s3(s3_path)

    db.delete(report)
    db.commit()
    return RedirectResponse("/cabinet/curator/reports?deleted=1", status_code=302)


# ── Portfolio split-panel ────────────────────────────────────────────────────

@router.get("/curator/portfolio", response_class=HTMLResponse)
def curator_portfolio(_user: Annotated[dict, Depends(require_curator)]):
    return RedirectResponse("/cabinet/students?tab=portfolio", status_code=302)


@router.get("/curator/portfolio/student/{student_id}")
def get_portfolio_data(
    student_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_student_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at

    # Limit: защита от медленных выборок при большом кол-ве работ
    before_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_BEFORE, Work.status == "success")
        .order_by(Work.created_at.desc())
        .limit(100)
        .all()
    )
    after_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_AFTER, Work.status == "success")
        .order_by(Work.created_at.desc())
        .limit(300)
        .all()
    )
    mock_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_MOCK_EXAM, Work.status == "success")
        .limit(100)
        .all()
    )
    scored = [w for w in mock_works if w.score is not None]
    avg_score = round(sum(float(w.score) for w in scored) / len(scored)) if scored else None
    after_by_month = group_works(after_works)

    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "avg_score": avg_score,
            "photo_url": student.photo_url,
        },
        "before_works": [
            {"s3_url": w.s3_url, "filename": w.filename, "id": w.id}
            for w in before_works
        ],
        "after_by_month": [
            {
                "month": g["month"],
                "year": g["year"],
                "total": g["total"],
                "works": [{"s3_url": w.s3_url, "filename": w.filename, "id": w.id} for w in g["works"]],
            }
            for g in after_by_month
        ],
    })


# ── Mock exams split-panel ───────────────────────────────────────────────────

@router.get("/curator/mock-exams", response_class=HTMLResponse)
def curator_mock_exams(_user: Annotated[dict, Depends(require_curator)]):
    return RedirectResponse("/cabinet/students?tab=mock-exams", status_code=302)


@router.get("/curator/mock-exams/student/{student_id}")
def get_mock_exams_data(
    student_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_student_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at

    # Limit: max 100 mock exams (защита от медленных выборок)
    mock_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_MOCK_EXAM, Work.status == "success")
        .order_by(Work.created_at.desc())
        .limit(100)
        .all()
    )
    scored = [w for w in mock_works if w.score is not None]
    avg_score = round(sum(float(w.score) for w in scored) / len(scored)) if scored else None

    works_by_subject: dict[str, list] = defaultdict(list)
    for w in mock_works:
        if w.subject:
            works_by_subject[w.subject].append(w)

    def serialize_mock_work(w: Work) -> dict:
        created_at = w.created_at
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        local_dt = created_at.astimezone(MSK_TZ) if created_at else None
        return {
            "id": w.id,
            "s3_url": w.s3_url,
            "filename": w.filename,
            "score": float(w.score) if w.score is not None else None,
            "comment": w.comment,
            "created_at": created_at.isoformat() if created_at else None,
            "work_date": local_dt.date().isoformat() if local_dt else "",
            "date_label": local_dt.strftime("%d.%m.%Y") if local_dt else "",
        }

    locks = {
        lock.subject: {"is_locked": lock.is_locked}
        for lock in db.query(MockExamLock).filter(MockExamLock.user_id == student_id).all()
    }

    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "avg_score": avg_score,
            "photo_url": student.photo_url,
        },
        "mock_works": {
            subject: [serialize_mock_work(w) for w in works_list]
            for subject, works_list in works_by_subject.items()
        },
        "mock_locks": locks,
    })


# ── Student card (backward compat) ───────────────────────────────────────────

@router.get("/students/{student_id}", response_class=HTMLResponse)
def student_card(student_id: int, _user: Annotated[dict, Depends(require_curator)]):
    """Перенаправляет на единый кабинет учеников."""
    return RedirectResponse(f"/cabinet/students?student={student_id}", status_code=302)


# ── Retakes split-panel ─────────────────────────────────────────────────────

@router.get("/curator/retakes", response_class=HTMLResponse)
def curator_retakes(_user: Annotated[dict, Depends(require_curator)]):
    return RedirectResponse("/cabinet/students?tab=retakes", status_code=302)


@router.get("/curator/retakes/student/{student_id}")
def get_retakes_data(
    student_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_student_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at

    retake_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_RETAKE, Work.status == "success")
        .order_by(Work.created_at.desc())
        .limit(100)
        .all()
    )
    retakes_by_month = group_works(retake_works)

    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "photo_url": student.photo_url,
        },
        "retakes_by_month": [
            {
                "month": g["month"],
                "year": g["year"],
                "total": g["total"],
                "works": [
                    {
                        "id": w.id,
                        "s3_url": w.s3_url,
                        "filename": w.filename,
                        "student_score": float(w.student_score) if w.student_score is not None else None,
                        "curator_score": float(w.score) if w.score is not None else None,
                        "comment": w.comment,
                    }
                    for w in g["works"]
                ],
            }
            for g in retakes_by_month
        ],
    })


# ── POST: unlock mock exam ───────────────────────────────────────────────────

@router.post("/mock-exam/unlock")
def unlock_mock_exam(
    student_id: Annotated[int, Form()],
    subject: Annotated[str, Form()],
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    redirect_to: str = Form(""),
):
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=400, detail="Неверный предмет")

    get_student_for_staff_access(
        db,
        user,
        student_id,
        not_found_status_code=403,
        not_found_detail="Нет доступа к этому студенту",
        forbidden_detail="Нет доступа к этому студенту",
    )

    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == student_id,
        MockExamLock.subject == subject,
    ).first()
    if lock:
        lock.is_locked = False
        lock.unlocked_at = datetime.now(timezone.utc)
        lock.unlocked_by_id = user["user_id"]
        db.commit()

    dest = redirect_to if (redirect_to and redirect_to.startswith("/") and not redirect_to.startswith("//")) else f"/cabinet/students/{student_id}?tab=exams"
    return RedirectResponse(dest, status_code=302)


# ── POST: score work ─────────────────────────────────────────────────────────

@router.post("/works/{work_id}/score")
def curator_score_work(
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

    work.score = max(0.0, min(100.0, score))
    work.comment = comment.strip() or None
    work.scored_at = datetime.now(timezone.utc)
    work.scored_by_id = user["user_id"]

    db.add(Notification(
        user_id=work.user_id,
        title=f"Куратор проверил вашу работу — {int(work.score)} / 100",
        text=work.comment if work.comment else None,
        work_id=work.id,
    ))
    db.commit()
    invalidate_unread(work.user_id)

    dest = redirect_to or f"/cabinet/students/{work.user_id}?tab=exams"
    return RedirectResponse(dest, status_code=302)
