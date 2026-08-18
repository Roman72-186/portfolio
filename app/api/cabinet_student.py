import logging
import re
from datetime import datetime, timezone
from typing import Annotated

logger = logging.getLogger(__name__)

from datetime import date

_PHONE_RE = re.compile(r'^[\d\s\+\-\(\)]{7,20}$')
_TG_RE = re.compile(r'^[A-Za-z0-9_]{4,32}$')

from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session, get_cached_unread, set_cached_unread, invalidate_unread
from app.config import settings
from app.constants import (
    MONTHS,
    TARIFFS,
    TARIFF_DISPLAY,
    ENROLLMENT_YEARS,
    MONTH_TO_NUM,
    FEATURE_PORTFOLIO_UPLOAD,
    MOCK_SUBJECTS,
    COURSE_PERIODS,
    LESSON_COUNTS,
    MANDATORY_COURSE_PERIOD,
)
from app.db.database import get_db
from app.dependencies import require_student, require_csrf
from app.models.notification import Notification
from app.models.upload_log import UploadLog
from app.models.user import User
from app.services.exam_cycle import get_active_ticket
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.feature_periods import is_feature_available
from app.services.mock_exam_access import (
    MOCK_EXAM_DURATION_SEC,
    is_mock_exam_attempt_open,
    is_subject_allowed_for_student,
    mock_exam_deadline_for_started_at,
)
from app.services.tz import MSK_TZ, today_msk
from app.services.user_management import log_tariff_change
from app.services.utils import study_duration_text, group_works
from app.services.video_catalog import list_published_videos
from app.tmpl import templates, format_ticket_description

MOCK_EXAM_PREVIEW = 4  # number of recent mock photos shown on cabinet home

router = APIRouter(prefix="/cabinet")


def _get_unread_count(user_id: int, db: DBSession) -> int:
    cached = get_cached_unread(user_id)
    if cached is not None:
        return cached
    count = db.query(func.count(Notification.id)).filter(
        Notification.user_id == user_id,
        Notification.is_read.is_(False),
    ).scalar() or 0
    set_cached_unread(user_id, count)
    return count

# Human-readable tariff labels for form display (value submitted is UPPER, label is title-case)
TARIFF_LABELS = list(TARIFF_DISPLAY.values())


def needs_profile_setup(user: dict) -> bool:
    """Профиль не заполнен — редиректить на /cabinet/profile вместо любой стартовой
    страницы ученика (/cabinet/student, /cabinet/tracker, /cabinet/learning)."""
    return not user["profile_completed"] or not user.get("course_periods") or not user.get("lessons_count")


@router.get("/student", response_class=HTMLResponse)
@router.get("/tracker", response_class=HTMLResponse)
def cabinet_student(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    # History of tariffs: distinct tariffs ordered by first upload date
    try:
        tariff_rows = (
            db.query(UploadLog.tariff, func.min(UploadLog.uploaded_at).label("first_used"))
            .filter(UploadLog.user_id == user["user_id"], UploadLog.status == "success")
            .group_by(UploadLog.tariff)
            .order_by(func.min(UploadLog.uploaded_at))
            .all()
        )
        tariff_history = [{"tariff": r.tariff, "first_used": r.first_used} for r in tariff_rows]
    except Exception as exc:
        logger.warning("tariff_history query failed for user_id=%s: %s", user["user_id"], exc)
        tariff_history = []

    if not tariff_history and user["tariff"]:
        tariff_history = [{"tariff": user["tariff"], "first_used": None}]

    enrolled_at = user.get("enrolled_at") or user.get("created_at")
    study_duration = study_duration_text(enrolled_at) if enrolled_at else None

    # Limit: max 100 recent mock exams (защита от медленных выборок)
    mock_works = (
        db.query(Work)
        .filter(
            Work.user_id == user["user_id"],
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.status == "success",
        )
        .order_by(Work.created_at.desc())
        .limit(100)
        .all()
    )
    mock_scored = [w for w in mock_works if w.score is not None]
    mock_avg = (
        round(sum(float(w.score) for w in mock_scored) / len(mock_scored))
        if mock_scored else None
    )

    avg_score_by_subject: dict = {}
    for subj in MOCK_SUBJECTS:
        subj_scored = [w for w in mock_scored if w.subject == subj]
        if subj_scored:
            avg_score_by_subject[subj] = round(
                sum(float(w.score) for w in subj_scored) / len(subj_scored)
            )

    # Текущий незакрытый цикл (если есть) + сколько финалов ждут оценки
    from app.models.exam_cycle import ExamCycle

    active_cycle_row = (
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user["user_id"], ExamCycle.closed_at.is_(None))
        .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
        .first()
    )
    active_cycle = None
    if active_cycle_row:
        pending_review_count = (
            db.query(Work)
            .filter(
                Work.cycle_id == active_cycle_row.id,
                Work.is_final == True,
                Work.score.is_(None),
                Work.status == "success",
            )
            .count()
        )
        active_cycle = {
            "subject": active_cycle_row.subject,
            "started_at": active_cycle_row.started_at,
            "pending_review_count": pending_review_count,
        }

    # Limit: max 100 recent retakes (защита от медленных выборок)
    retake_works = (
        db.query(Work)
        .filter(
            Work.user_id == user["user_id"],
            Work.work_type == WORK_TYPE_RETAKE,
            Work.status == "success",
        )
        .order_by(Work.created_at.desc())
        .limit(100)
        .all()
    )

    notifications = (
        db.query(Notification)
        .filter(
            Notification.user_id == user["user_id"],
            Notification.is_read.is_(False),
        )
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    unread_count = len(notifications)

    # Активные попытки пробников
    from app.models.mock_exam_attempt import MockExamAttempt
    active_attempts_q = (
        db.query(MockExamAttempt)
        .filter(
            MockExamAttempt.user_id == user["user_id"],
            MockExamAttempt.completed_at.is_(None),
            MockExamAttempt.expired_at.is_(None),
        )
        .order_by(MockExamAttempt.started_at)
        .all()
    )
    active_attempts = [
        {
            "subject": a.subject,
            "ticket_title": a.ticket_title,
            "started_at": a.started_at.isoformat(),
            "expires_at": mock_exam_deadline_for_started_at(a.started_at).isoformat(),
        }
        for a in active_attempts_q
        if is_mock_exam_attempt_open(a.started_at)
        and is_subject_allowed_for_student(db, user["user_id"], a.subject)
    ]

    return templates.TemplateResponse("cabinet_student.html", {
        "request": request,
        "user": user,
        "tariff_history": tariff_history,
        "study_duration": study_duration,
        "mock_count": len(mock_works),
        "mock_avg": mock_avg,
        "avg_score_by_subject": avg_score_by_subject,
        "active_cycle": active_cycle,
        "mock_recent": mock_works[:MOCK_EXAM_PREVIEW],
        "retake_count": len(retake_works),
        "retake_recent": retake_works[:MOCK_EXAM_PREVIEW],
        "notifications": notifications,
        "unread_count": unread_count,
        "active_attempts": active_attempts,
        "mock_exam_duration_sec": MOCK_EXAM_DURATION_SEC,
        # Пункт меню показываем, только если ученику открыт хотя бы один урок:
        # иначе он придёт в каталог, где все темы ещё не начались, и увидит пустоту.
        "video_module_available": (
            user.get("role_rank", 0) >= 2 or user.get("is_group_member", False)
        ) and bool(list_published_videos(db, viewer=user)),
    })


def _profile_template_ctx(request, user, errors=None, form=None):
    return {
        "request": request,
        "user": user,
        "tariffs": TARIFF_LABELS,
        "tariff_display": TARIFF_DISPLAY,
        "months": MONTHS,
        "enrollment_years": ENROLLMENT_YEARS,
        "university_years": list(range(2015, 2032)),
        "course_periods": COURSE_PERIODS,
        "lesson_counts": LESSON_COUNTS,
        "mandatory_course_period": MANDATORY_COURSE_PERIOD,
        **({"errors": errors} if errors else {}),
        **({"form": form} if form else {}),
    }


@router.get("/profile", response_class=HTMLResponse)
def profile_get(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
):
    has_periods = bool(user.get("course_periods")) and bool(user.get("lessons_count"))
    if user["profile_completed"] and has_periods:
        return RedirectResponse("/cabinet/learning", status_code=302)

    form = None
    if user["profile_completed"]:
        enrolled_at = user.get("enrolled_at")
        tariff = user.get("tariff") or ""
        past_tariffs_raw = user.get("past_tariffs") or ""
        course_periods_raw = user.get("course_periods") or ""
        form = {
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "phone": user.get("phone") or "",
            "parent_phone": user.get("parent_phone") or "",
            "tariff": TARIFF_DISPLAY.get(tariff, tariff),
            "tg_username": user.get("tg_username") or "",
            "enrollment_month": enrolled_at.month if enrolled_at else user.get("enrollment_year") and None,
            "enrollment_year": enrolled_at.year if enrolled_at else user.get("enrollment_year"),
            "university_year": user.get("university_year"),
            "past_tariffs": [TARIFF_DISPLAY.get(t, t) for t in past_tariffs_raw.split(",") if t],
            "course_periods": [p for p in course_periods_raw.split(",") if p],
            "lessons_count": user.get("lessons_count") or "",
        }
    return templates.TemplateResponse("profile.html", _profile_template_ctx(request, user, form=form))


@router.post("/profile", response_class=HTMLResponse)
def profile_post(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    phone: Annotated[str, Form()],
    parent_phone: Annotated[str, Form()],
    tariff: Annotated[str, Form()],
    tg_username: Annotated[str, Form()] = "",
    enrollment_month: Annotated[str, Form()] = "",
    enrollment_year: Annotated[str, Form()] = "",
    university_year: Annotated[str, Form()] = "",
    about: Annotated[str, Form()] = "",
    past_tariffs: Annotated[list[str], Form()] = [],
    course_periods: Annotated[list[str], Form()] = [],
    lessons_count: Annotated[str, Form()] = "",
):
    errors = []
    first_name = first_name.strip()
    last_name = last_name.strip()
    phone = phone.strip()
    parent_phone = parent_phone.strip()
    tariff = tariff.strip().upper()
    tg_username = tg_username.strip().lstrip("@")

    # university_year — required
    parsed_university_year: int | None = None
    if university_year.strip():
        try:
            _uy = int(university_year.strip())
            if 2000 <= _uy <= 2100:
                parsed_university_year = _uy
            else:
                errors.append("Год поступления в ВУЗ должен быть реальным годом")
        except ValueError:
            errors.append("Год поступления в ВУЗ должен быть числом")
    else:
        errors.append("Укажите год поступления в ВУЗ")

    # Parse month + year → enrolled_at
    parsed_month: int | None = None
    parsed_year: int | None = None
    parsed_enrolled_at: datetime | None = None

    if enrollment_month.strip():
        try:
            _m = int(enrollment_month.strip())
            if 1 <= _m <= 12:
                parsed_month = _m
            else:
                errors.append("Выберите месяц присоединения")
        except ValueError:
            errors.append("Выберите месяц присоединения")
    else:
        errors.append("Укажите месяц присоединения к курсу")

    if enrollment_year.strip():
        try:
            parsed_year = int(enrollment_year.strip())
            if not (2000 <= parsed_year <= 2100):
                errors.append("Год поступления должен быть реальным годом")
        except ValueError:
            errors.append("Год поступления должен быть числом")
    else:
        errors.append("Укажите год поступления")

    if parsed_month and parsed_year:
        parsed_enrolled_at = datetime(parsed_year, parsed_month, 1, tzinfo=timezone.utc)

    if not first_name:
        errors.append("Введите имя")
    elif len(first_name) > 50:
        errors.append("Имя слишком длинное (максимум 50 символов)")
    if not last_name:
        errors.append("Введите фамилию")
    elif len(last_name) > 50:
        errors.append("Фамилия слишком длинная (максимум 50 символов)")
    if not phone:
        errors.append("Введите номер телефона")
    elif not _PHONE_RE.match(phone):
        errors.append("Введите корректный номер телефона (только цифры, пробелы, +, -, скобки)")
    if not parent_phone:
        errors.append("Введите номер телефона родителя")
    elif not _PHONE_RE.match(parent_phone):
        errors.append("Введите корректный номер телефона родителя")
    if not tg_username:
        errors.append("Укажите ник в Telegram")
    elif not _TG_RE.match(tg_username):
        errors.append("Ник Telegram: только латиница, цифры, _ (4–32 символа)")
    if tariff not in TARIFFS:
        errors.append("Выберите тариф")

    past_tariffs = [t.upper() for t in past_tariffs if t.upper() in TARIFFS and t.upper() != tariff]

    course_periods = [p for p in course_periods if p in COURSE_PERIODS]
    if MANDATORY_COURSE_PERIOD not in course_periods:
        course_periods.insert(0, MANDATORY_COURSE_PERIOD)
    lessons_count = lessons_count.strip()
    if lessons_count not in LESSON_COUNTS:
        errors.append("Выберите количество занятий")

    if errors:
        form = {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "parent_phone": parent_phone,
            "tariff": TARIFF_DISPLAY.get(tariff, tariff),
            "tg_username": tg_username,
            "enrollment_month": parsed_month,
            "enrollment_year": parsed_year,
            "university_year": parsed_university_year,
            "past_tariffs": past_tariffs,
            "course_periods": course_periods,
            "lessons_count": lessons_count,
        }
        return templates.TemplateResponse("profile.html",
            _profile_template_ctx(request, user, errors=errors, form=form))

    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    log_tariff_change(db, db_user.id, db_user.id, db_user.tariff, tariff)
    db_user.first_name = first_name
    db_user.last_name = last_name
    db_user.name = f"{first_name} {last_name}"
    db_user.phone = phone
    db_user.parent_phone = parent_phone
    db_user.tariff = tariff
    db_user.tg_username = tg_username or None
    db_user.enrollment_year = parsed_year
    db_user.enrolled_at = parsed_enrolled_at
    db_user.university_year = parsed_university_year
    db_user.past_tariffs = ",".join(past_tariffs) if past_tariffs else None
    db_user.course_periods = ",".join(course_periods) if course_periods else None
    db_user.lessons_count = lessons_count or None
    db_user.profile_completed = True
    if db_user.profile_completed_at is None:
        db_user.profile_completed_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_session(user["session_id"])

    return RedirectResponse("/cabinet/learning", status_code=302)


@router.get("/notifications", response_class=HTMLResponse)
def cabinet_notifications(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    notifications = (
        db.query(Notification)
        .filter(Notification.user_id == user["user_id"])
        .order_by(Notification.is_read.asc(), Notification.created_at.desc())
        .limit(100)
        .all()
    )
    unread_count = sum(1 for n in notifications if not n.is_read)
    # Mark all as read
    if unread_count:
        db.query(Notification).filter(
            Notification.user_id == user["user_id"],
            Notification.is_read.is_(False),
        ).update({"is_read": True, "read_at": datetime.now(timezone.utc)})
        db.commit()
        invalidate_unread(user["user_id"])
    return templates.TemplateResponse("cabinet_notifications.html", {
        "request": request,
        "user": user,
        "notifications": notifications,
        "unread_count": unread_count,
    })


@router.get("/notifications/feed")
def notifications_feed(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """JSON-лента уведомлений для попапа колокольчика. Только чтение —
    отметка о прочтении идёт отдельным POST /notifications/mark-read."""
    notifications = (
        db.query(Notification)
        .filter(Notification.user_id == user["user_id"])
        .order_by(Notification.is_read.asc(), Notification.created_at.desc())
        .limit(30)
        .all()
    )
    items = [
        {
            "title": n.title,
            "text": n.text or "",
            "is_read": n.is_read,
            "work_id": n.work_id,
            "created_at": n.created_at.strftime("%d.%m.%Y %H:%M") if n.created_at else "",
        }
        for n in notifications
    ]
    unread_count = sum(1 for n in notifications if not n.is_read)
    return JSONResponse({"notifications": items, "unread_count": unread_count})


@router.post("/notifications/mark-read")
def mark_notifications_read(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    db.query(Notification).filter(
        Notification.user_id == user["user_id"],
        Notification.is_read.is_(False),
    ).update({"is_read": True, "read_at": datetime.now(timezone.utc)})
    db.commit()
    invalidate_unread(user["user_id"])
    return JSONResponse({"ok": True})


# ── GET /cabinet/portfolio ────────────────────────────────────────────────────

PAGE_SIZE = 10


@router.get("/cycle", response_class=HTMLResponse)
def cabinet_cycle_hub(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    tab: str = Query(default="feedback"),
):
    """Экран ученика: только вкладка «Обратная связь» (диалог по циклам)."""
    from app.models.exam_cycle import ExamCycle
    from app.models.feedback import Feedback
    from app.models.notification import Notification

    # Все циклы пользователя — открытые (сверху) и закрытые (снизу).
    cycles_q = (
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user["user_id"])
        .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
        .all()
    )

    open_cycles: list[dict] = []
    closed_cycles: list[dict] = []
    if cycles_q:
        cycle_ids = [c.id for c in cycles_q]
        finals_by_cycle: dict[int, list[Work]] = {}
        for w in (
            db.query(Work)
            .filter(Work.cycle_id.in_(cycle_ids), Work.is_final == True)  # noqa: E712
            .all()
        ):
            finals_by_cycle.setdefault(w.cycle_id, []).append(w)
        all_work_ids = [w.id for ws in finals_by_cycle.values() for w in ws]
        unread_work_ids: set[int] = set()
        if all_work_ids:
            unread_work_ids = {
                row[0] for row in db.query(Notification.work_id).filter(
                    Notification.user_id == user["user_id"],
                    Notification.work_id.in_(all_work_ids),
                    Notification.is_read == False,  # noqa: E712
                ).all()
            }
        for c in cycles_q:
            finals = finals_by_cycle.get(c.id, [])
            scored_finals = [
                w for w in finals
                if w.work_type == WORK_TYPE_MOCK_EXAM and w.score is not None
            ]
            if not scored_finals:
                scored_finals = [w for w in finals if w.score is not None]
            close_score = None
            if scored_finals:
                close_work = max(
                    scored_finals,
                    key=lambda w: (
                        w.scored_at or w.created_at or datetime.min,
                        w.id or 0,
                    ),
                )
                close_score = float(close_work.score)
            item = {
                "id": c.id,
                "subject": c.subject,
                "started_at": c.started_at.isoformat(),
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                "close_score": close_score,
                "attempts": len(finals),
                "unread_count": sum(1 for w in finals if w.id in unread_work_ids),
            }
            if c.closed_at is None:
                open_cycles.append(item)
            else:
                closed_cycles.append(item)

    cycles_count = len(cycles_q)
    unread = _get_unread_count(user["user_id"], db)

    return templates.TemplateResponse("cabinet_cycle.html", {
        "request": request,
        "user": user,
        "open_cycles": open_cycles,
        "closed_cycles": closed_cycles,
        "cycles_count": cycles_count,
        "unread_count": unread,
        "active_tab": "cycle",
    })


@router.get("/portfolio", response_class=HTMLResponse)
async def cabinet_portfolio(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Portfolio tab: before works as a flat gallery, after works grouped by year-month."""
    from app.services.drive import list_student_photos
    portfolio_upload_open, _ = is_feature_available(db, FEATURE_PORTFOLIO_UPLOAD)

    before_works = (
        db.query(Work)
        .filter(
            Work.user_id == user["user_id"],
            Work.work_type == WORK_TYPE_BEFORE,
            Work.status == "success",
        )
        .order_by(Work.year, Work.month, Work.created_at)
        .limit(500)
        .all()
    )
    after_works = (
        db.query(Work)
        .filter(
            Work.user_id == user["user_id"],
            Work.work_type == WORK_TYPE_AFTER,
            Work.status == "success",
        )
        .order_by(Work.year, Work.month, Work.created_at)
        .limit(500)
        .all()
    )

    # Пробные экзамены: финалки ЗАКРЫТЫХ циклов в формате дневного календаря
    # (по предметам, со score/этапами) — тот же сборщик, что и во вкладке Пробники.
    mock_works_by_subject = _collect_cycle_works(
        db, user["user_id"], WORK_TYPE_MOCK_EXAM, closed_only=True
    )
    mock_subjects = list(MOCK_SUBJECTS)
    if "Без предмета" in mock_works_by_subject:
        mock_subjects.append("Без предмета")
    has_mock = any(mock_works_by_subject.get(s) for s in mock_subjects)

    # Fetch Drive thumbnail URLs for works that came from Drive (no s3_url)
    drive_thumbnails: dict[str, str] = {}
    all_works = before_works + after_works
    needs_thumb = any(w.drive_file_id and not w.s3_url for w in all_works)
    if settings.n8n_enabled and needs_thumb and user.get("tg_username"):
        photos = await list_student_photos(
            vk_id=user["vk_id"],
            tariff=user.get("tariff", ""),
            tg_username=user["tg_username"],
        )
        drive_thumbnails = {p["id"]: p["thumbnail_url"] for p in photos if p.get("id") and p.get("thumbnail_url")}

    def serialize_portfolio_group(group: dict) -> dict:
        return {
            "year": group["year"],
            "month": group["month"],
            "total": group["total"],
            "works": [
                {
                    "id": w.id,
                    "filename": w.filename,
                    "thumb": w.s3_url or (drive_thumbnails.get(w.drive_file_id, "") if w.drive_file_id else ""),
                }
                for w in group["works"]
            ],
        }

    before_groups = group_works(before_works)
    after_groups = group_works(after_works)

    return templates.TemplateResponse("cabinet_portfolio.html", {
        "request": request,
        "user": user,
        "can_upload_portfolio_after": bool(portfolio_upload_open),
        "before_works": before_works,
        "before_groups": before_groups,
        "after_groups": after_groups,
        "portfolio_before_groups": [serialize_portfolio_group(g) for g in before_groups],
        "portfolio_after_groups": [serialize_portfolio_group(g) for g in after_groups],
        "mock_works_by_subject": mock_works_by_subject,
        "mock_subjects": mock_subjects,
        "has_mock": has_mock,
        "months": MONTHS,
        "current_year": today_msk().year,
        "page_size": PAGE_SIZE,
        "unread_count": _get_unread_count(user["user_id"], db),
        "drive_thumbnails": drive_thumbnails,
    })


# ── GET /cabinet/cycle/probnik & /cabinet/cycle/otrabotka ────────────────────

def _collect_cycle_works(
    db: DBSession,
    user_id: int,
    work_type: str,
    *,
    closed_only: bool = False,
) -> dict[str, list[dict]]:
    """Календарь Цикла Пробника: финалки + промежуточные + feedback по предметам.

    closed_only=True — только финалы из ЗАКРЫТЫХ циклов (для Портфолио →
    Пробные экзамены: показываем уже оценённые/завершённые).
    """
    from app.models.feedback import Feedback, FeedbackPhoto, FeedbackMessage
    from app.models.exam_cycle import ExamCycle
    from app.models.exam_assignment import ExamTicket

    finals_q = (
        db.query(Work)
        .filter(
            Work.user_id == user_id,
            Work.work_type == work_type,
            Work.status == "success",
        )
    )
    if closed_only:
        # Портфолио → Пробные экзамены: показываем все ОЦЕНЁННЫЕ пробники
        # (score проставлен) — это и есть «последняя финальная фотография,
        # оценённая последней». Условие покрывает оба источника данных:
        #   • новый flow — финал Пробника с выставленным баллом (балл ставится
        #     раньше закрытия цикла, см. close_cycle — закрытие отдельное и
        #     ручное, но появление в Портфолио привязано к самому баллу);
        #   • легаси /upload/mock-exam — работы с cycle_id IS NULL, is_final=false,
        #     которые НЕ попали бы под старый фильтр is_final + closed cycle.
        # parent_work_id IS NULL отсекает этапные (intermediate): они никогда не
        # оцениваются и не должны попадать в Портфолио.
        finals_q = finals_q.filter(
            Work.score.isnot(None),
            Work.parent_work_id.is_(None),
        )
    else:
        finals_q = finals_q.filter(Work.is_final == True)  # noqa: E712
    finals = (
        finals_q
        .order_by(Work.year, Work.month, Work.created_at)
        .limit(300)
        .all()
    )
    final_ids = [w.id for w in finals]

    intermediates_by_parent: dict[int, list[Work]] = {}
    if final_ids:
        for w in (
            db.query(Work)
            .filter(Work.parent_work_id.in_(final_ids), Work.is_final == False)  # noqa: E712
            .order_by(Work.created_at)
            .all()
        ):
            intermediates_by_parent.setdefault(w.parent_work_id, []).append(w)

    feedbacks_by_work: dict[int, Feedback] = {}
    fb_photos_by_fb: dict[int, list[FeedbackPhoto]] = {}
    if final_ids:
        feedbacks_by_work = {
            f.work_id: f
            for f in db.query(Feedback).filter(Feedback.work_id.in_(final_ids)).all()
        }
        fb_ids = [f.id for f in feedbacks_by_work.values()]
        if fb_ids:
            for ph in (
                db.query(FeedbackPhoto)
                .filter(FeedbackPhoto.feedback_id.in_(fb_ids))
                .order_by(FeedbackPhoto.order_idx, FeedbackPhoto.id)
                .all()
            ):
                fb_photos_by_fb.setdefault(ph.feedback_id, []).append(ph)

    # Портфолио → Пробные экзамены: в качестве фото финалки показываем ПОСЛЕДНЕЕ
    # фото ученика из диалога ОС (та работа, после которой выставлен балл), а не
    # исходную загрузку. Цикл закрывается при простановке балла, а в закрытый цикл
    # писать нельзя (см. feedback.post_dialog_message), поэтому «последнее фото
    # ученика» ⟺ «фото, после которого проставлена оценка». Фоллбэк — w.s3_url
    # (закрытый цикл без фото-ответа ученика / легаси-загрузка).
    last_student_photo_by_work: dict[int, str] = {}
    if closed_only and feedbacks_by_work:
        fb_to_work = {f.id: wid for wid, f in feedbacks_by_work.items()}
        for m in (
            db.query(FeedbackMessage)
            .filter(
                FeedbackMessage.feedback_id.in_(list(fb_to_work.keys())),
                FeedbackMessage.sender_role == "student",
                FeedbackMessage.photo_s3_url.isnot(None),
            )
            .order_by(FeedbackMessage.created_at, FeedbackMessage.id)
            .all()
        ):
            wid = fb_to_work.get(m.feedback_id)
            if wid is not None:
                last_student_photo_by_work[wid] = m.photo_s3_url  # asc → последнее выигрывает

    cycles_by_id: dict[int, ExamCycle] = {}
    cycle_ids = {w.cycle_id for w in finals if w.cycle_id}
    if cycle_ids:
        cycles_by_id = {
            c.id: c
            for c in db.query(ExamCycle).filter(ExamCycle.id.in_(list(cycle_ids))).all()
        }

    ticket_title_by_cycle: dict[int, str] = {}
    if cycle_ids:
        for cid, title in (
            db.query(ExamCycle.id, ExamTicket.title)
            .outerjoin(ExamTicket, ExamCycle.ticket_id == ExamTicket.id)
            .filter(ExamCycle.id.in_(list(cycle_ids)))
            .all()
        ):
            if title:
                ticket_title_by_cycle[cid] = title

    def _serialize(w: Work) -> dict:
        created = w.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        local_dt = created.astimezone(MSK_TZ) if created else None
        fb = feedbacks_by_work.get(w.id)
        fb_payload = None
        if fb:
            fb_payload = {
                "id": fb.id,
                "greeting": fb.greeting,
                "strengths": fb.strengths,
                "weaknesses": fb.weaknesses,
                "recommendations": fb.recommendations,
                "updated_at": fb.updated_at.isoformat() if fb.updated_at else None,
                "photos": [
                    {"id": ph.id, "s3_url": ph.s3_url}
                    for ph in fb_photos_by_fb.get(fb.id, [])
                ],
            }
        cycle = cycles_by_id.get(w.cycle_id) if w.cycle_id else None
        display_url = last_student_photo_by_work.get(w.id) or w.s3_url
        return {
            "id": w.id,
            "subject": w.subject or "",
            "s3_url": display_url,
            "filename": w.filename,
            "score": float(w.score) if w.score is not None else None,
            "student_score": float(w.student_score) if w.student_score is not None else None,
            "comment": w.comment,
            "created_at": created.isoformat() if created else None,
            "work_date": local_dt.date().isoformat() if local_dt else "",
            "date_label": local_dt.strftime("%d.%m.%Y") if local_dt else "",
            "attempt_number": w.attempt_number,
            "cycle_id": w.cycle_id,
            "cycle_started_at": cycle.started_at.isoformat() if cycle and cycle.started_at else None,
            "cycle_closed_at": cycle.closed_at.isoformat() if cycle and cycle.closed_at else None,
            "ticket_title": ticket_title_by_cycle.get(w.cycle_id) if w.cycle_id else None,
            "intermediates": [
                {"id": i.id, "s3_url": i.s3_url, "filename": i.filename}
                for i in intermediates_by_parent.get(w.id, [])
            ],
            "feedback": fb_payload,
        }

    by_subject: dict[str, list[dict]] = {s: [] for s in MOCK_SUBJECTS}
    unassigned: list[dict] = []
    for w in finals:
        payload = _serialize(w)
        if w.subject in by_subject:
            by_subject[w.subject].append(payload)
        else:
            unassigned.append(payload)
    if unassigned:
        by_subject["Без предмета"] = unassigned
    return by_subject


def render_cycle_calendar(
    request: Request,
    user: dict,
    db: DBSession,
    *,
    target_user_id: int,
    work_type: str,
    page_title: str,
    upload_url: str,
    upload_label: str,
    feature_key: str,
    active_tab: str,
    staff_view: bool = False,
    student_name: str | None = None,
    back_url: str = "/cabinet/cycle",
    back_label: str = "К Циклу Пробника",
):
    from app.constants import FEATURE_LABELS

    works_by_subject = _collect_cycle_works(db, target_user_id, work_type)
    subjects = list(MOCK_SUBJECTS)
    if "Без предмета" in works_by_subject:
        subjects.append("Без предмета")
    upload_open, upload_msg = is_feature_available(db, feature_key)
    return templates.TemplateResponse("cabinet_cycle_calendar.html", {
        "request": request,
        "user": user,
        "page_title": page_title,
        "work_type": work_type,
        "subjects": subjects,
        "works_by_subject": works_by_subject,
        "upload_url": upload_url,
        "upload_label": upload_label,
        "upload_open": upload_open,
        "upload_msg": upload_msg or FEATURE_LABELS.get(feature_key, ""),
        "months": MONTHS,
        "current_year": today_msk().year,
        "active_tab": active_tab,
        "unread_count": _get_unread_count(user["user_id"], db),
        "staff_view": staff_view,
        "student_name": student_name,
        "back_url": back_url,
        "back_label": back_label,
    })


# /cycle/probnik и /cycle/otrabotka объединены в двухвкладочный /cycle (редизайн 2026-05-23).
# Старые URL студента редиректят на новую страницу для обратной совместимости со ссылками
# из уведомлений и закладок.

@router.get("/cycle/probnik", response_class=HTMLResponse)
def cabinet_cycle_probnik_redirect(_user: Annotated[dict, Depends(require_student)]):
    return RedirectResponse("/cabinet/cycle?tab=mock", status_code=302)


@router.get("/cycle/otrabotka", response_class=HTMLResponse)
def cabinet_cycle_otrabotka_redirect(_user: Annotated[dict, Depends(require_student)]):
    return RedirectResponse("/cabinet/cycle?tab=mock", status_code=302)


# ── GET /cabinet/scores удалён в редизайне 2026-05-23 ────────────────────────

# ── GET /cabinet/api/exam-ticket ──────────────────────────────────────────────

@router.get("/api/exam-ticket")
def get_exam_ticket(
    subject: Annotated[str, Query()],
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Return the active exam ticket for given subject, if any."""
    ticket = get_active_ticket(db, user["user_id"], subject)
    if not ticket:
        return JSONResponse({"found": False})
    return JSONResponse({
        "found": True,
        "ticket": {
            "id": ticket.id,
            "title": ticket.title,
            "description": format_ticket_description(ticket.description),
            "image_url": ticket.image_s3_url or "",
            "end_date": ticket.end_date.isoformat(),
        },
    })
