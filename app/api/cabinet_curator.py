from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.constants import MOCK_SUBJECTS, TARIFFS, TARIFF_DISPLAY
from app.db.database import get_db
from app.dependencies import require_curator, require_admin_role, require_csrf
from app.models.mock_exam_lock import MockExamLock
from app.models.notification import Notification
from app.models.user import User
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.stats import curator_avg_scores, mock_period_subject_stats
from app.services.tz import MSK_TZ
from app.services.utils import study_duration_text, group_works, has_case_growth
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")

PAGE_SIZE = 10
TARIFF_LABELS = list(TARIFF_DISPLAY.values())


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
        .filter(User.curator_id == curator_id, User.is_active == True)
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
        "cohort_tag": s.cohort_tag,
        "tariff": s.tariff,
        "exam_dates": s.exam_dates,
        "exam_subjects": s.exam_subjects,
        "study_mode": s.study_mode,
        "is_publishable": s.is_publishable,
        "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
        "avg_score": avg_score,
        "upload_count": len(works),
        "portfolio_do_completed": s.portfolio_do_completed,
        "has_case": has_case_growth(mock_works),
    }


def _check_student_access(student_id: int, user: dict, db: DBSession) -> User:
    student = db.query(User).filter(User.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    if student.curator_id != user["user_id"] and user.get("role_rank", 0) < 3:
        raise HTTPException(status_code=403, detail="Нет доступа к этому ученику")
    return student


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/curator", response_class=HTMLResponse)
def cabinet_curator_dashboard(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    students = _get_curator_students(user["user_id"], db)
    period_start, period_end = _current_academic_period()
    mock_subject_period, mock_subject_stats = mock_period_subject_stats(db, curator_id=user["user_id"])
    avg_scores = curator_avg_scores(db, curator_id=user["user_id"])
    return templates.TemplateResponse("cabinet_curator_dashboard.html", {
        "request": request,
        "user": user,
        "students_count": len(students),
        "period_start": period_start,
        "period_end": period_end,
        "mock_subject_period": mock_subject_period,
        "mock_subject_stats": mock_subject_stats,
        "avg_scores": avg_scores,
    })


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
            "cohort_tag": student.cohort_tag,
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
            "cohort_tag": student.cohort_tag,
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
            "cohort_tag": student.cohort_tag,
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

    student = db.query(User).filter(User.id == student_id).first()
    if not student or (student.curator_id != user["user_id"] and user.get("role_rank", 0) < 3):
        raise HTTPException(status_code=403, detail="Нет доступа к этому студенту")

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
