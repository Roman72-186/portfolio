"""
Единый роутер карточки ученика для всех ролей персонала.

Доступ:
  - rank=2 (куратор)   — только свои студенты, только просмотр
  - rank=3 (модератор) — заглушка, нет доступа
  - rank=4 (админ)     — все студенты, оценивание + разблокировка
  - rank=5 (суперадмин) — все студенты, оценивание + разблокировка
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session, invalidate_unread
from app.constants import FEATURE_MOCK_EXAM, MOCK_SUBJECTS, MONTHS, TARIFFS, COHORT_TAGS, COHORT_TAG_LABELS
from app.db.database import get_db
from app.dependencies import get_current_user, require_admin_role, require_csrf
from app.models.session import Session
from app.models.exam_cycle import ExamCycle
from app.models.mock_exam_lock import MockExamLock
from app.models.notification import Notification
from app.models.role import Role
from app.models.upload_log import UploadLog
from app.models.user import User
from app.models.work import (
    Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER,
    WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE,
)
from app.services import s3 as s3_service
from app.services.exam_cycle import get_active_ticket, has_submitted_for_ticket
from app.services.feature_periods import get_active_period
from app.services.student_access import get_student_for_staff_access
from app.services.tz import MSK_TZ, msk_midnight
from app.services.utils import compress_image, study_duration_text, group_works, has_case_growth
from app.tmpl import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cabinet")


def _delete_work_rows_with_dependents(db: DBSession, works: list[Work]) -> int:
    """Delete selected works and direct stage-photo dependents in FK-safe order."""
    by_id = {w.id: w for w in works}
    final_ids = [w.id for w in works if w.is_final]
    if final_ids:
        for child in (
            db.query(Work)
            .filter(Work.parent_work_id.in_(final_ids))
            .all()
        ):
            by_id[child.id] = child

    ordered = sorted(by_id.values(), key=lambda w: 1 if w.is_final else 0)
    for work in ordered:
        if work.s3_path:
            s3_service.delete_from_s3(work.s3_path)
        db.delete(work)
    return len(ordered)


# ── Access control ────────────────────────────────────────────────────────────

def _require_student_panel(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Разрешает доступ куратору (rank=2) и admin/superadmin (rank>=4).
    Модератор (rank=3) — заглушка, нет доступа к студентам."""
    rank = user["role_rank"]
    if rank == 2 or rank >= 4:
        return user
    raise HTTPException(status_code=403, detail="Нет доступа")


def _get_accessible_students(
    user: dict,
    db: DBSession,
    *,
    has_unchecked_mocks: bool = False,
    mock_period_submitted: bool = False,
    show_hidden: bool = False,
) -> list:
    """Возвращает список студентов доступных текущему пользователю.

    Жёсткие фильтры (`has_unchecked_mocks`, `mock_period_submitted`)
    применяются только для admin/superadmin (rank>=4). Для куратора
    игнорируются.

    По умолчанию скрыты студенты без выбранных периодов/занятий.
    Суперадмин может раскрыть их через show_hidden=True.
    """
    hide_pre_cohort = not (show_hidden and user["role_rank"] >= 5)

    if user["role_rank"] == 2:
        # Куратор ВСЕГДА видит всех своих активных учеников, включая тех, кто
        # ещё не завершил онбординг (пустые course_periods/lessons_count).
        # period/lessons заполняет сам ученик в своём профиле (персонал — не может),
        # поэтому только-что привязанный ученик иначе «пропадал» у куратора без
        # сигнала. Незавершённые помечаются бейджем needs_setup в сайдбаре.
        return (
            db.query(User)
            .filter(User.curator_id == user["user_id"], User.is_active == True)
            .order_by(User.last_name, User.first_name)
            .all()
        )

    # rank >= 4: все активные ученики
    student_role = db.query(Role).filter(Role.rank == 1).first()
    if not student_role:
        return []

    q = db.query(User).filter(User.role_id == student_role.id, User.is_active == True)
    if hide_pre_cohort:
        q = q.filter(User.course_periods.isnot(None), User.lessons_count.isnot(None))

    if has_unchecked_mocks or mock_period_submitted:
        active_period = get_active_period(db, FEATURE_MOCK_EXAM)
        if not active_period:
            # Фильтры требуют активного периода — его нет → пустая выборка.
            return []
        _mp_start = msk_midnight(active_period.start_date)
        _mp_end = msk_midnight(active_period.end_date + timedelta(days=1))

        sub = db.query(Work.user_id).filter(
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.status == "success",
            Work.created_at >= _mp_start,
            Work.created_at < _mp_end,
        )
        if has_unchecked_mocks:
            sub = sub.filter(Work.score.is_(None))
        q = q.filter(User.id.in_(sub.distinct()))

    return q.order_by(User.last_name, User.first_name).all()


def _parse_bool(s: str) -> bool:
    return s.lower() in ("1", "true", "yes", "on")


def _check_access(student_id: int, user: dict, db: DBSession) -> User:
    return get_student_for_staff_access(
        db,
        user,
        student_id,
        active_only=True,
        not_found_detail="Ученик не найден",
        forbidden_detail="Нет доступа к этому ученику",
    )


def _enrich(s: User, counts_by_user: dict, avg_by_user: dict,
            mock_counts_by_user: dict | None = None,
            unchecked_by_user: dict | None = None,
            scored_subjects_by_user: dict | None = None,
            has_case_by_user: dict | None = None) -> dict:
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
        "course_periods": s.course_periods,
        "lessons_count": s.lessons_count,
        "has_case": has_case_by_user.get(s.id, False) if has_case_by_user else False,
        "avg_score": avg_by_user.get(s.id),
        "upload_count": counts_by_user.get(s.id, 0),
        "curator_id": s.curator_id or 0,
        "enrollment_year": s.enrollment_year or 0,
        "tg_username": (s.tg_username or "").lstrip("@").lower(),
        "vk_id": s.vk_id or "",
        "mock_count": mock_counts_by_user.get(s.id, 0) if mock_counts_by_user else 0,
        "unchecked": unchecked_by_user.get(s.id, 0) if unchecked_by_user else 0,
        "scored_subjects": scored_subjects_by_user.get(s.id, []) if scored_subjects_by_user else [],
        # Незавершённый онбординг: ученик не выбрал период обучения / кол-во занятий.
        # Точно совпадает с набором, который раньше скрывался от куратора (pre-cohort).
        "needs_setup": not (s.course_periods and s.lessons_count),
    }


# ── Main page ─────────────────────────────────────────────────────────────────

@router.get("/students", response_class=HTMLResponse)
def students_panel(
    request: Request,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
    student: int = Query(0),
    tab: str = Query("portfolio"),
    has_unchecked_mocks: str = Query(""),
    mock_period_submitted: str = Query(""),
    show_hidden: str = Query(""),
):
    is_admin_panel = user["role_rank"] >= 4
    has_unchecked = is_admin_panel and _parse_bool(has_unchecked_mocks)
    mock_submitted = is_admin_panel and _parse_bool(mock_period_submitted)
    show_hidden_b = user["role_rank"] >= 5 and _parse_bool(show_hidden)

    students = _get_accessible_students(
        user, db,
        has_unchecked_mocks=has_unchecked,
        mock_period_submitted=mock_submitted,
        show_hidden=show_hidden_b,
    )

    active_hard_filters: list[dict] = []
    if has_unchecked:
        active_hard_filters.append({"key": "has_unchecked_mocks", "label": "Непроверенные пробники"})
    if mock_submitted:
        active_hard_filters.append({"key": "mock_period_submitted", "label": "Сдавал в текущий период"})
    if show_hidden_b:
        active_hard_filters.append({"key": "show_hidden", "label": "Показаны без периодов"})

    counts_by_user: dict = {}
    avg_by_user: dict = {}
    if students:
        student_ids = [s.id for s in students]
        # Aggregate upload counts per user — O(students) not O(works)
        count_rows = (
            db.query(Work.user_id, func.count(Work.id).label("cnt"))
            .filter(Work.user_id.in_(student_ids), Work.status == "success")
            .group_by(Work.user_id)
            .all()
        )
        counts_by_user = {r.user_id: r.cnt for r in count_rows}

        # Aggregate avg mock-exam score per user
        avg_rows = (
            db.query(Work.user_id, func.avg(Work.score).label("avg"))
            .filter(
                Work.user_id.in_(student_ids),
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.status == "success",
                Work.score.isnot(None),
            )
            .group_by(Work.user_id)
            .all()
        )
        avg_by_user = {r.user_id: round(float(r.avg)) for r in avg_rows}

    mock_counts_by_user: dict = {}
    unchecked_by_user: dict = {}
    scored_subjects_by_user: dict = defaultdict(list)
    has_case_by_user: dict[int, bool] = {}
    if students:
        _ids_all = [s.id for s in students]
        case_works = (
            db.query(Work.user_id, Work.subject, Work.score, Work.month, Work.year,
                     Work.scored_at, Work.created_at, Work.work_type)
            .filter(
                Work.user_id.in_(_ids_all),
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.status == "success",
                Work.score.isnot(None),
                Work.subject.isnot(None),
            )
            .all()
        )
        works_by_uid: dict[int, list] = defaultdict(list)
        for w in case_works:
            works_by_uid[w.user_id].append(w)
        for uid, ws in works_by_uid.items():
            has_case_by_user[uid] = has_case_growth(ws)

    can_score = user["role_rank"] >= 4
    if students and can_score:
        _ids = [s.id for s in students]
        mock_count_rows = (
            db.query(Work.user_id, func.count(Work.id).label("cnt"))
            .filter(
                Work.user_id.in_(_ids),
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.status == "success",
            )
            .group_by(Work.user_id)
            .all()
        )
        mock_counts_by_user = {r.user_id: r.cnt for r in mock_count_rows}

        unchecked_rows = (
            db.query(Work.user_id, func.count(Work.id).label("cnt"))
            .filter(
                Work.user_id.in_(_ids),
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.status == "success",
                Work.score.is_(None),
            )
            .group_by(Work.user_id)
            .all()
        )
        unchecked_by_user = {r.user_id: r.cnt for r in unchecked_rows}

        scored_subj_rows = (
            db.query(Work.user_id, Work.subject)
            .filter(
                Work.user_id.in_(_ids),
                Work.work_type == WORK_TYPE_MOCK_EXAM,
                Work.status == "success",
                Work.score.isnot(None),
                Work.subject.isnot(None),
            )
            .distinct()
            .all()
        )
        for r in scored_subj_rows:
            scored_subjects_by_user[r.user_id].append(r.subject)

    sidebar_students = [
        _enrich(
            s,
            counts_by_user,
            avg_by_user,
            mock_counts_by_user,
            unchecked_by_user,
            scored_subjects_by_user,
            has_case_by_user,
        )
        for s in students
    ]
    # ── Mock exam submission status by active ticket (per subject) ───────────
    # Оба предмета (Рисунок, Композиция) могут иметь активный билет одновременно —
    # «сдал» означает сдачу финала по КАЖДОМУ предмету, у которого сейчас есть
    # активный билет, назначенный этому ученику (резолвер — get_active_ticket).
    mock_status_available = False
    submitted_students: list[dict] = []
    not_submitted_students: list[dict] = []
    if user["role_rank"] == 2 and students:
        # Резолвер вызывает несколько запросов на пару (ученик × предмет) —
        # считаем каждую пару ровно один раз, а не дважды (any() + основной цикл).
        active_ticket_by_key = {
            (s["id"], subject): get_active_ticket(db, s["id"], subject)
            for s in sidebar_students
            for subject in MOCK_SUBJECTS
        }
        any_ticket_active = any(t is not None for t in active_ticket_by_key.values())

        if any_ticket_active:
            mock_status_available = True
            for s in sidebar_students:
                pending_subjects = []
                has_active_ticket = False
                for subject in MOCK_SUBJECTS:
                    ticket = active_ticket_by_key[(s["id"], subject)]
                    if ticket:
                        has_active_ticket = True
                        if not has_submitted_for_ticket(db, s["id"], subject, ticket.id):
                            pending_subjects.append(subject)
                if not has_active_ticket:
                    # У ученика сейчас нет назначенного активного билета —
                    # не считать «сдал» и не показывать в чейс-листе «не сдали».
                    continue
                entry = {
                    "id": s["id"], "name": s["name"], "tg_username": s["tg_username"],
                    "pending_subjects": pending_subjects,
                }
                (not_submitted_students if pending_subjects else submitted_students).append(entry)

    sidebar_title = "Мои ученики" if user["role_rank"] == 2 else "Все ученики"
    valid_tabs = ("portfolio", "mock-exams", "cycles", "statistics")
    show_curator_filter = user["role_rank"] >= 4

    # Curator list for admin filter
    curators: list[dict] = []
    if show_curator_filter:
        curator_role = db.query(Role).filter(Role.rank == 2).first()
        if curator_role:
            curator_users = (
                db.query(User)
                .filter(User.role_id == curator_role.id, User.is_active == True)
                .order_by(User.last_name, User.first_name)
                .all()
            )
            curators = [
                {"id": c.id, "name": f"{c.last_name or ''} {c.first_name or c.name}".strip()}
                for c in curator_users
            ]

    # Distinct enrollment years
    enrollment_years = sorted(
        {s.enrollment_year for s in students if s.enrollment_year},
        reverse=True,
    )

    return templates.TemplateResponse("cabinet_students.html", {
        "request": request,
        "user": user,
        "sidebar_students": sidebar_students,
        "initial_student_id": student,
        "initial_tab": tab if tab in valid_tabs else "portfolio",
        "nav_active": "statistics" if (tab in valid_tabs and tab == "statistics") else "students",
        "can_score": can_score,
        "sidebar_title": sidebar_title,
        "mock_subjects": MOCK_SUBJECTS,
        "months": MONTHS,
        "current_year": datetime.now(timezone.utc).year,
        "show_curator_filter": show_curator_filter,
        "curators": curators,
        "enrollment_years": enrollment_years,
        "active_hard_filters": active_hard_filters,
        "is_admin_panel": is_admin_panel,
        "is_superadmin": user["role_rank"] >= 5,
        "cohort_tag_labels": COHORT_TAG_LABELS,
        "mock_status_available": mock_status_available,
        "submitted_students": submitted_students,
        "not_submitted_students": not_submitted_students,
    })


# ── AJAX: profile ────────────────────────────────────────────────────────────

@router.get("/students/{student_id}/profile")
def get_student_profile(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at

    # Curator name — db.get() hits identity map first (no extra query if already loaded)
    curator_name = None
    if student.curator_id:
        curator = db.get(User, student.curator_id)
        if curator:
            curator_name = f"{curator.last_name or ''} {curator.first_name or curator.name}".strip()

    # Work stats
    works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.status == "success")
        .all()
    )
    portfolio_count = sum(1 for w in works if w.work_type in (WORK_TYPE_BEFORE, WORK_TYPE_AFTER))
    mock_works = [w for w in works if w.work_type == WORK_TYPE_MOCK_EXAM]
    retake_count = sum(1 for w in works if w.work_type == WORK_TYPE_RETAKE)
    scored = [w for w in mock_works if w.score is not None]
    avg_score = round(sum(float(w.score) for w in scored) / len(scored)) if scored else None
    cycle_count = (
        db.query(func.count(ExamCycle.id))
        .filter(ExamCycle.user_id == student_id)
        .scalar()
    ) or 0

    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "first_name": student.first_name,
            "last_name": student.last_name,
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
            "phone": student.phone,
            "parent_phone": student.parent_phone,
            "tg_username": student.tg_username,
            "vk_id": student.vk_id,
            "about": student.about,
            "tariff": student.tariff or "—",
            "past_tariffs": student.past_tariffs,
            "study_mode": student.study_mode,
            "has_case": has_case_growth(works),
            "course_periods": student.course_periods,
            "lessons_count": student.lessons_count,
            "enrollment_year": student.enrollment_year,
            "university_year": student.university_year,
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "is_group_member": student.is_group_member,
            "profile_completed": student.profile_completed,
            "curator_name": curator_name,
            "avg_score": avg_score,
            "portfolio_count": portfolio_count,
            "mock_exam_count": len(mock_works),
            "retake_count": retake_count,
            "cycle_count": cycle_count,
        },
    })


# ── AJAX: portfolio ───────────────────────────────────────────────────────────

@router.get("/students/{student_id}/portfolio")
def get_portfolio(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at

    before_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_BEFORE, Work.status == "success")
        .order_by(Work.created_at.desc()).limit(100).all()
    )
    after_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_AFTER, Work.status == "success")
        .order_by(Work.created_at.desc()).limit(300).all()
    )
    mock_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_MOCK_EXAM, Work.status == "success")
        .limit(100).all()
    )
    scored = [w for w in mock_works if w.score is not None]
    avg_score = round(sum(float(w.score) for w in scored) / len(scored)) if scored else None

    # Финалки Пробника из ЗАКРЫТЫХ циклов — для секции «Пробные экзамены».
    # Тот же дневной календарь (CYCCAL), что и в Портфолио ученика: одинаков для всех ролей.
    from app.api.cabinet_student import _collect_cycle_works  # lazy: избегаем циклического импорта
    mock_works_by_subject = _collect_cycle_works(
        db, student_id, WORK_TYPE_MOCK_EXAM, closed_only=True
    )
    mock_subjects = list(MOCK_SUBJECTS)
    if "Без предмета" in mock_works_by_subject:
        mock_subjects.append("Без предмета")

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
        "before_by_month": [
            {
                "month": g["month"], "year": g["year"], "total": g["total"],
                "works": [{"s3_url": w.s3_url, "filename": w.filename, "id": w.id} for w in g["works"]],
            }
            for g in group_works(before_works)
        ],
        "after_by_month": [
            {
                "month": g["month"], "year": g["year"], "total": g["total"],
                "works": [{"s3_url": w.s3_url, "filename": w.filename, "id": w.id} for w in g["works"]],
            }
            for g in group_works(after_works)
        ],
        "mock_works_by_subject": mock_works_by_subject,
        "mock_subjects": mock_subjects,
    })


# ── AJAX: mock exams ──────────────────────────────────────────────────────────

@router.get("/students/{student_id}/mock-exams")
def get_mock_exams(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
    period_only: str = Query(""),
):
    student = _check_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at

    period_only_bool = period_only.lower() in ("1", "true", "yes", "on")
    active_period = get_active_period(db, FEATURE_MOCK_EXAM) if period_only_bool else None

    q = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_MOCK_EXAM, Work.status == "success")
    )
    if active_period:
        _mp_start = msk_midnight(active_period.start_date)
        _mp_end = msk_midnight(active_period.end_date + timedelta(days=1))
        q = q.filter(Work.created_at >= _mp_start, Work.created_at < _mp_end)
    elif period_only_bool:
        # Period requested, but none active → show nothing
        q = q.filter(Work.id < 0)
    mock_works = q.order_by(Work.created_at.desc()).limit(100).all()
    scored = [w for w in mock_works if w.score is not None]
    avg_score = round(sum(float(w.score) for w in scored) / len(scored)) if scored else None

    avg_score_by_subject: dict = {}
    for subj in MOCK_SUBJECTS:
        subj_scored = [w for w in mock_works if w.subject == subj and w.score is not None]
        if subj_scored:
            avg_score_by_subject[subj] = round(
                sum(float(w.score) for w in subj_scored) / len(subj_scored)
            )

    works_by_subject: dict = defaultdict(list)
    for w in mock_works:
        if w.subject:
            works_by_subject[w.subject].append(w)

    # Какие work_id имеют feedback — нужно для бейджа на кнопке
    from app.models.feedback import Feedback as _FB
    mock_ids = [w.id for w in mock_works]
    fb_work_ids: set[int] = set()
    if mock_ids:
        fb_work_ids = {row[0] for row in db.query(_FB.work_id).filter(_FB.work_id.in_(mock_ids)).all()}

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
            "cycle_id": w.cycle_id,
            "has_feedback": w.id in fb_work_ids,
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
        "avg_score_by_subject": avg_score_by_subject,
        "period_only": period_only_bool,
        "period_active": bool(active_period),
    })


# ── AJAX: statistics (динамика баллов по пробникам) ──────────────────────────

@router.get("/students/{student_id}/statistics")
def get_statistics(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    from app.services.stats import student_score_curve

    student = _check_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at
    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
        },
        "points": student_score_curve(db, student_id),
    })


# ── AJAX: retakes ─────────────────────────────────────────────────────────────

@router.get("/students/{student_id}/retakes")
def get_retakes(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_access(student_id, user, db)
    enrolled_at = student.enrolled_at or student.created_at

    retake_works = (
        db.query(Work)
        .filter(
            Work.user_id == student_id,
            Work.status == "success",
            or_(
                Work.work_type == WORK_TYPE_RETAKE,
                and_(Work.work_type == WORK_TYPE_MOCK_EXAM, Work.sent_to_retake == True),
            ),
        )
        .order_by(Work.created_at.desc()).limit(100).all()
    )

    from app.models.feedback import Feedback as _FB
    r_ids = [w.id for w in retake_works]
    r_fb_ids: set[int] = set()
    if r_ids:
        r_fb_ids = {row[0] for row in db.query(_FB.work_id).filter(_FB.work_id.in_(r_ids)).all()}

    def _work_dict(w):
        return {
            "id": w.id, "s3_url": w.s3_url, "filename": w.filename,
            "month": w.month, "year": w.year,
            "student_score": float(w.student_score) if w.student_score is not None else None,
            "curator_score": float(w.score) if w.score is not None else None,
            "comment": w.comment,
            "is_mock": w.work_type == WORK_TYPE_MOCK_EXAM,
            "subject": w.subject or "",
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "cycle_id": w.cycle_id,
            "has_feedback": w.id in r_fb_ids,
        }

    retakes_by_subject: dict[str, list] = {}
    for s in MOCK_SUBJECTS:
        retakes_by_subject[s] = []
    retakes_unassigned: list = []
    for w in retake_works:
        d = _work_dict(w)
        if d["subject"] in retakes_by_subject:
            retakes_by_subject[d["subject"]].append(d)
        else:
            retakes_unassigned.append(d)

    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
        },
        "can_move_retakes": user.get("role_rank", 0) >= 5,
        "subjects": list(MOCK_SUBJECTS),
        "retakes_by_subject": retakes_by_subject,
        "retakes_unassigned": retakes_unassigned,
        "retakes_by_month": [
            {
                "month": g["month"], "year": g["year"], "total": g["total"],
                "works": [_work_dict(w) for w in g["works"]],
            }
            for g in group_works(retake_works)
        ],
    })


# ── POST: оценить работу ──────────────────────────────────────────────────────

@router.post("/students/{student_id}/retakes/{work_id}/subject")
def move_retake_to_subject(
    student_id: int,
    work_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    subject: str = Form(...),
):
    if user["role_rank"] < 5:
        raise HTTPException(status_code=403, detail="Доступно только суперадмину")
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=400, detail="Неверный предмет")

    work = db.query(Work).filter(
        Work.id == work_id,
        Work.user_id == student_id,
        Work.status == "success",
        or_(
            Work.work_type == WORK_TYPE_RETAKE,
            and_(Work.work_type == WORK_TYPE_MOCK_EXAM, Work.sent_to_retake == True),
        ),
    ).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")

    work.subject = subject
    db.commit()
    return JSONResponse({"ok": True, "work_id": work.id, "subject": subject})


@router.post("/students/{student_id}/works/{work_id}/score")
def score_work(
    student_id: int,
    work_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    score: float = Form(...),
    comment: str = Form(""),
    tab: str = Form("mock-exams"),
):
    work = db.query(Work).filter(Work.id == work_id, Work.user_id == student_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if tab not in ("portfolio", "mock-exams", "retakes"):
        tab = "mock-exams"

    if not (0 <= score <= 100):
        raise HTTPException(status_code=422, detail="Балл должен быть от 0 до 100")
    work.score = int(round(score))
    work.comment = (comment.strip() or None)
    if work.comment and len(work.comment) > 500:
        work.comment = work.comment[:500]
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
    return RedirectResponse(
        f"/cabinet/students?student={student_id}&tab={tab}&saved=1", status_code=302
    )


# ── POST: отправить пробник на отработку (оценка + комментарий) ───────────────

@router.post("/students/{student_id}/mock-exams/{work_id}/retake")
def send_mock_exam_to_retake(
    student_id: int,
    work_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    score: float = Form(...),
    comment: str = Form(...),
):
    work = db.query(Work).filter(
        Work.id == work_id,
        Work.user_id == student_id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
    ).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if not (0 <= score <= 100):
        raise HTTPException(status_code=422, detail="Балл должен быть от 0 до 100")
    comment_clean = comment.strip()
    if not comment_clean:
        raise HTTPException(status_code=422, detail="Комментарий обязателен при отправке на отработку")
    if len(comment_clean) > 500:
        comment_clean = comment_clean[:500]

    work.score = int(round(score))
    work.comment = comment_clean
    work.scored_at = datetime.now(timezone.utc)
    work.scored_by_id = user["user_id"]
    work.sent_to_retake = True

    db.add(Notification(
        user_id=work.user_id,
        title=f"Пробник отправлен на отработку — {int(work.score)} / 100",
        text=f"{comment_clean}\n\nМожно загрузить отработку в разделе «Отработка».",
        work_id=work.id,
    ))
    db.commit()
    invalidate_unread(work.user_id)

    return JSONResponse({"ok": True, "score": int(round(score)), "comment": comment_clean})


# ── POST: вернуть пробник на доработку (только разблокировка, без оценки) ────

@router.post("/students/{student_id}/mock-exams/{work_id}/revision")
def send_mock_exam_to_revision(
    student_id: int,
    work_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    if user["role_rank"] < 5:
        raise HTTPException(status_code=403, detail="Доступно только суперадмину")

    work = db.query(Work).filter(
        Work.id == work_id,
        Work.user_id == student_id,
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.is_final == True,  # noqa: E712
    ).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")

    if work.cycle_id is not None:
        cycle = db.query(ExamCycle).filter(ExamCycle.id == work.cycle_id).first()
        if cycle is not None and cycle.closed_at is not None:
            raise HTTPException(
                status_code=409,
                detail="Цикл уже закрыт с оценкой — отправка на доработку недоступна",
            )

    # Снимаем реальную блокировку пересдачи: has_submitted_for_ticket больше не
    # видит этот финал как сдачу по билету → /upload/probnik/final пройдёт через
    # _overwrite_final и перезапишет это же фото (см. exam_cycle.has_submitted_for_ticket).
    work.needs_revision = True

    subject = work.subject
    if subject and subject in MOCK_SUBJECTS:
        lock = db.query(MockExamLock).filter(
            MockExamLock.user_id == student_id,
            MockExamLock.subject == subject,
        ).first()
        if lock:
            lock.is_locked = False
            lock.unlocked_at = datetime.now(timezone.utc)
            lock.unlocked_by_id = user["user_id"]

    db.add(Notification(
        user_id=student_id,
        title="Пробник возвращён на доработку",
        text="Загрузи новые фото выполненного задания.",
    ))
    db.commit()
    return JSONResponse({"ok": True})


# ── POST: разблокировать пробник ──────────────────────────────────────────────

@router.post("/students/{student_id}/mock-exams/unlock")
def unlock_mock_exam(
    student_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    subject: str = Form(...),
):
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=400, detail="Неверный предмет")

    lock = db.query(MockExamLock).filter(
        MockExamLock.user_id == student_id,
        MockExamLock.subject == subject,
    ).first()
    if lock:
        lock.is_locked = False
        lock.unlocked_at = datetime.now(timezone.utc)
        lock.unlocked_by_id = user["user_id"]
        db.commit()

    return RedirectResponse(
        f"/cabinet/students?student={student_id}&tab=mock-exams", status_code=302
    )


# ── POST: редактировать анкету ученика ───────────────────────────────────────

@router.post("/students/{student_id}/profile")
def edit_student_profile(
    student_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    first_name: str = Form(""),
    last_name: str = Form(""),
    phone: str = Form(""),
    parent_phone: str = Form(""),
    tg_username: str = Form(""),
    tariff: str = Form(""),
    enrollment_year: str = Form(""),
    university_year: str = Form(""),
    cohort_tag: str = Form(""),
):
    student = _check_access(student_id, user, db)

    errors = []
    first_name = first_name.strip()
    last_name = last_name.strip()
    phone = phone.strip()
    parent_phone = parent_phone.strip()
    tg_username = tg_username.strip().lstrip("@")
    tariff = tariff.strip().upper()
    cohort_tag = cohort_tag.strip().lower()

    if not first_name:
        errors.append("Имя обязательно")
    if not last_name:
        errors.append("Фамилия обязательна")
    if not phone:
        errors.append("Телефон обязателен")
    if tariff and tariff not in TARIFFS:
        errors.append("Неверный тариф")
    if cohort_tag and cohort_tag not in COHORT_TAGS:
        errors.append("Неверная метка набора")

    parsed_enrollment_year = None
    if enrollment_year.strip():
        try:
            parsed_enrollment_year = int(enrollment_year.strip())
            if not (2000 <= parsed_enrollment_year <= 2100):
                errors.append("Нереальный год поступления")
        except ValueError:
            errors.append("Год поступления должен быть числом")

    parsed_university_year = None
    if university_year.strip():
        try:
            parsed_university_year = int(university_year.strip())
            if not (2000 <= parsed_university_year <= 2100):
                errors.append("Нереальный год ВУЗ")
        except ValueError:
            errors.append("Год ВУЗ должен быть числом")

    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)

    student.first_name = first_name
    student.last_name = last_name
    student.name = f"{first_name} {last_name}"
    if phone:
        student.phone = phone
    if parent_phone:
        student.parent_phone = parent_phone
    if tg_username:
        student.tg_username = tg_username
    if tariff:
        student.tariff = tariff
    if parsed_enrollment_year is not None:
        student.enrollment_year = parsed_enrollment_year
    if parsed_university_year is not None:
        student.university_year = parsed_university_year
    student.cohort_tag = cohort_tag or None
    db.commit()

    # Invalidate all cached sessions for this student
    sessions = db.query(Session).filter(
        Session.user_id == student_id, Session.is_active == True
    ).all()
    for s in sessions:
        invalidate_session(s.id)

    return JSONResponse({"ok": True})


# ── POST: загрузка работ админом за ученика ──────────────────────────────────

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_SIZE = 10 * 1024 * 1024
MAX_FILES = 10

WORK_TYPE_LABELS = {
    "before": "До", "after": "После",
    "mock_exam": "Пробник", "retake": "Отработка",
}


def _is_allowed_image(content_type: str | None, filename: str | None) -> bool:
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return True
    if ct in ("application/octet-stream", ""):
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""
        return ext in _ALLOWED_EXTENSIONS
    return False


@router.post("/students/{student_id}/upload")
async def admin_upload_works(
    student_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    photos: list[UploadFile] = File(...),
    work_type: str = Form(...),
    month: str = Form(""),
    year: int | None = Form(None),
    subject: str = Form(""),
    mock_date: str = Form(""),
    score: str = Form(""),
):
    student = _check_access(student_id, user, db)

    valid_types = {WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE}
    if work_type not in valid_types:
        return JSONResponse({"ok": False, "error": "Неверный тип работы"}, status_code=400)
    if not student.tariff:
        return JSONResponse({"ok": False, "error": "У ученика не указан тариф"}, status_code=400)
    if not student.vk_id:
        return JSONResponse({"ok": False, "error": "У ученика нет VK ID"}, status_code=400)

    if not photos or (len(photos) == 1 and not photos[0].filename):
        return JSONResponse({"ok": False, "error": "Выберите хотя бы одно фото"}, status_code=400)
    if len(photos) > MAX_FILES:
        return JSONResponse({"ok": False, "error": f"Максимум {MAX_FILES} фото"}, status_code=400)

    work_score = None
    work_created_at = None
    if work_type == WORK_TYPE_MOCK_EXAM:
        if subject not in MOCK_SUBJECTS:
            return JSONResponse({"ok": False, "error": "Укажите предмет для пробника"}, status_code=400)
        try:
            parsed_date = datetime.strptime(mock_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "Укажите дату пробника"}, status_code=400)
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "Укажите балл за пробник"}, status_code=400)
        if not (0 <= score_value <= 100):
            return JSONResponse({"ok": False, "error": "Балл должен быть от 0 до 100"}, status_code=400)
        month = MONTHS[parsed_date.month - 1]
        year = parsed_date.year
        work_score = int(round(score_value))
        work_created_at = msk_midnight(parsed_date)
    elif work_type == WORK_TYPE_RETAKE:
        if subject not in MOCK_SUBJECTS:
            return JSONResponse({"ok": False, "error": "Укажите предмет для отработки: Рисунок или Композиция"}, status_code=400)
        if month not in MONTHS:
            return JSONResponse({"ok": False, "error": "Неверный месяц"}, status_code=400)
        if year is None:
            return JSONResponse({"ok": False, "error": "Укажите год"}, status_code=400)
    else:
        if month not in MONTHS:
            return JSONResponse({"ok": False, "error": "Неверный месяц"}, status_code=400)
        if year is None:
            return JSONResponse({"ok": False, "error": "Укажите год"}, status_code=400)

    # Read and validate files
    files_data = []
    for photo in photos:
        if not _is_allowed_image(photo.content_type, photo.filename):
            return JSONResponse({"ok": False, "error": f"Файл «{photo.filename}» — неподдерживаемый формат"}, status_code=400)
        photo_bytes = await photo.read()
        if len(photo_bytes) > MAX_SIZE:
            return JSONResponse({"ok": False, "error": f"Файл «{photo.filename}» слишком большой (макс. 10 МБ)"}, status_code=400)
        files_data.append((photo.filename or "photo.jpg", photo_bytes))

    vk_id = student.vk_id
    tariff = student.tariff

    def _build_s3_path(filename: str) -> str:
        if work_type == WORK_TYPE_BEFORE:
            return s3_service.s3_path_before(vk_id, tariff, filename)
        if work_type == WORK_TYPE_MOCK_EXAM:
            return s3_service.s3_path_mock_exam(vk_id, tariff, filename)
        if work_type == WORK_TYPE_RETAKE:
            return s3_service.s3_path_retake(vk_id, tariff, filename)
        return s3_service.s3_path_after(vk_id, tariff, filename)

    success_count = 0
    fail_count = 0
    loop = asyncio.get_running_loop()

    def _compress_and_upload_s3(raw: bytes, path: str):
        compressed = compress_image(raw)
        url = s3_service.upload_to_s3(path, compressed, "image/jpeg")
        return compressed, url

    # Цикл Пробника: получить/создать для mock_exam/retake
    cycle_id: int | None = None
    attempt_no: int | None = None
    if work_type in (WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE) and subject:
        from app.services import exam_cycle as cycle_service
        if work_type == WORK_TYPE_MOCK_EXAM:
            cycle, _created = cycle_service.get_or_create_cycle_for_probnik(
                db, user_id=student_id, subject=subject, ticket_id=None,
            )
        else:
            cycle = cycle_service.find_latest_cycle(db, student_id, subject)
            if cycle is None:
                cycle, _created = cycle_service.get_or_create_cycle_for_probnik(
                    db, user_id=student_id, subject=subject, ticket_id=None,
                )
        cycle_id = cycle.id
        attempt_no = cycle_service.next_attempt_number(
            db, cycle_id=cycle_id, work_type=work_type,
        )

    for fname, raw_bytes in files_data:
        s3_path = _build_s3_path(fname)
        try:
            compressed, s3_url = await loop.run_in_executor(
                None, _compress_and_upload_s3, raw_bytes, s3_path
            )

            work = Work(
                user_id=student_id,
                work_type=work_type,
                month=month,
                year=year,
                filename=fname,
                s3_url=s3_url,
                s3_path=s3_path,
                subject=subject if work_type in (WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE) else None,
                tariff=tariff,
                score=work_score,
                scored_at=datetime.now(timezone.utc) if work_score is not None else None,
                scored_by_id=user["user_id"] if work_score is not None else None,
                status="success",
                uploaded_by_id=user["user_id"],
                created_at=work_created_at or datetime.now(timezone.utc),
                cycle_id=cycle_id,
                is_final=True if cycle_id else None,
                attempt_number=attempt_no,
            )
            db.add(work)
            db.add(UploadLog(
                user_id=student_id,
                student_name=f"{student.last_name or ''} {student.first_name or student.name}".strip(),
                tariff=tariff,
                month=month,
                photo_type=work_type,
                photo_count=1,
                status="success",
            ))
            success_count += 1
        except Exception as exc:
            logger.error("Admin upload failed for %s: %s", fname, exc)
            fail_count += 1

    if success_count > 0:
        db.commit()

    return JSONResponse({"ok": True, "success_count": success_count, "fail_count": fail_count})


# ── DELETE: удалить "папку" (все работы за месяц/тип) ────────────────────────

@router.delete("/students/{student_id}/works/bulk")
async def bulk_delete_works(
    student_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    import json
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
    except Exception:
        body = {}

    work_type = body.get("work_type", "")
    month = body.get("month", "")
    year = body.get("year")

    if not work_type or not month or not year:
        return JSONResponse({"ok": False, "error": "Укажите work_type, month и year"}, status_code=400)

    _check_access(student_id, user, db)

    works = (
        db.query(Work)
        .filter(
            Work.user_id == student_id,
            Work.work_type == work_type,
            Work.month == month,
            Work.year == int(year),
        )
        .all()
    )

    if not works:
        return JSONResponse({"ok": True, "deleted_count": 0})

    deleted_count = _delete_work_rows_with_dependents(db, works)

    db.commit()
    return JSONResponse({"ok": True, "deleted_count": deleted_count})


# ── DELETE: удалить работу (фото) ученика ────────────────────────────────────

@router.patch("/students/{student_id}/portfolio/month")
async def rename_portfolio_month(
    student_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if user["role_rank"] < 5:
        raise HTTPException(status_code=403, detail="Доступно только суперадмину")

    import json
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
    except Exception:
        body = {}

    work_type = body.get("work_type", WORK_TYPE_AFTER)
    from_month = body.get("from_month", "")
    from_year = body.get("from_year")
    to_month = body.get("to_month", "")
    to_year = body.get("to_year")

    if work_type != WORK_TYPE_AFTER:
        return JSONResponse({"ok": False, "error": "Переименовывать можно только месяцы После обучения"}, status_code=400)
    if from_month not in MONTHS or to_month not in MONTHS:
        return JSONResponse({"ok": False, "error": "Неверный месяц"}, status_code=400)
    try:
        from_year_int = int(from_year)
        to_year_int = int(to_year)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "Неверный год"}, status_code=400)

    _check_access(student_id, user, db)
    works = (
        db.query(Work)
        .filter(
            Work.user_id == student_id,
            Work.work_type == WORK_TYPE_AFTER,
            Work.month == from_month,
            Work.year == from_year_int,
            Work.status == "success",
        )
        .all()
    )
    for work in works:
        work.month = to_month
        work.year = to_year_int

    db.commit()
    return JSONResponse({"ok": True, "updated_count": len(works)})


@router.patch("/students/{student_id}/portfolio/works/{work_id}/move")
async def move_portfolio_work(
    student_id: int,
    work_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if user["role_rank"] < 5:
        raise HTTPException(status_code=403, detail="Доступно только суперадмину")

    import json
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
    except Exception:
        body = {}

    to_month = body.get("to_month", "")
    to_year = body.get("to_year")
    if to_month not in MONTHS:
        return JSONResponse({"ok": False, "error": "Неверный месяц"}, status_code=400)
    try:
        to_year_int = int(to_year)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "Неверный год"}, status_code=400)

    _check_access(student_id, user, db)
    work = db.query(Work).filter(
        Work.id == work_id,
        Work.user_id == student_id,
        Work.work_type == WORK_TYPE_AFTER,
    ).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")

    work.month = to_month
    work.year = to_year_int
    db.commit()
    return JSONResponse({"ok": True})


@router.delete("/students/{student_id}/works/{work_id}")
def delete_work(
    student_id: int,
    work_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    work = db.query(Work).filter(Work.id == work_id, Work.user_id == student_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")

    _delete_work_rows_with_dependents(db, [work])
    db.commit()
    return JSONResponse({"ok": True})
