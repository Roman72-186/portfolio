"""
Единый роутер карточки ученика для всех ролей персонала.

Доступ:
  - rank=2 (куратор)   — только свои студенты; с 01.09.2026 (решение владельца,
    plans/2026-09-01-apparchi-student-centric-review.md) может ставить балл
    Work (`score_work`) — остальное на карточке по-прежнему только просмотр
  - rank=3 (преподаватель) — только свои студенты
  - rank=4 (админ/ГП)  — все студенты, оценивание + разблокировка, архив прошлых
    потоков на чтение (`/cabinet/archive`)
  - rank=5 (суперадмин) — всё то же, что rank=4
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session, invalidate_unread
from app.constants import REPORT_EXCLUDED_USER_IDS
from app.constants import MOCK_SUBJECTS, MONTHS, MONTH_TO_NUM, TARIFFS, TARIFFS_CURRENT, TARIFF_DISPLAY, COHORT_TAGS, COHORT_TAG_LABELS, TIMEZONE_DISPLAY
from app.db.database import get_db
from app.dependencies import (
    get_current_user,
    require_admin_role,
    require_csrf,
    require_csrf_header,
    require_curator,
    require_scorer,
)
from app.models.session import Session
from app.models.exam_assignment import ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.legacy_portfolio_photo import LegacyPortfolioPhoto
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.mock_exam_lock import MockExamLock
from app.models.notification import Notification
from app.services.rbac import can_score as role_can_score
from app.services.section_access import has_grant
from app.services.notify import notify
from app.services.point_a import maybe_notify_point_a_level, student_point_a
from app.services.review_aggregate import (
    DOMAIN_BLOCK_WORK, DOMAIN_HOMEWORK, DOMAIN_TASK_BLOCK, FULL_ACCESS_RANK,
    student_review_items,
)
from app.models.role import Role
from app.models.upload_log import UploadLog
from app.models.user import User
from app.models.work import (
    Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER,
    WORK_TYPE_MOCK_EXAM,
)
from app.services import s3 as s3_service
from app.services.exam_cycle import get_active_ticket, has_submitted_for_ticket
from app.services.stats import avg_score_by_subject_all_time
from app.services.portfolio import after_gallery_groups, item_source, portfolio_item_count
from app.services.student_access import get_student_for_staff_access
from app.services.user_management import apply_tariff_change, tariff_change_clears_access
from app.services.works import WorkHasFeedbackError, delete_works_with_dependents, upload_work_thumb
from app.services.tz import MSK_TZ, msk_input_value, msk_midnight, parse_msk_local
from app.services.utils import compress_image, study_duration_text, has_case_growth
from app.tmpl import format_rich_text, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cabinet")


# Тело удаления переехало в `app/services/works.py` (18.09.2026): ту же
# операцию делает роут ученика на `/upload`, а хранилище и порядок FK должны
# сниматься одинаково у обоих. Имя оставлено локальным алиасом — на него
# ссылаются вызовы ниже по файлу.
_delete_work_rows_with_dependents = delete_works_with_dependents


# ── Access control ────────────────────────────────────────────────────────────

def _require_student_panel(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Разрешает доступ персоналу начиная с куратора (rank>=2)."""
    rank = user["role_rank"]
    if rank >= 2:
        return user
    raise HTTPException(status_code=403, detail="Нет доступа")


def _can_read_archive(user: dict) -> bool:
    """Архив прошлых потоков: ГП и суперадмин, плюс куратор, которому
    суперадмин открыл архив лично (владелец 30.09.2026, `section_access.py`).
    Такой куратор видит весь архив школы, только на чтение: среди действующих
    учеников правило «только свои» остаётся (`student_access` снимает его лишь
    для архивного ученика в режиме чтения архива)."""
    return user["role_rank"] >= 4 or has_grant(user, "archive")


def _get_accessible_students(
    user: dict,
    db: DBSession,
    *,
    show_hidden: bool = False,
    archived: bool = False,
    has_access_deadline: bool = False,
) -> list:
    """Возвращает список студентов доступных текущему пользователю.

    Фильтров «Непроверенные пробники» и «Сдавал в текущий период» больше нет
    (владелец 29.09.2026): оба требовали активного окна `FeaturePeriod`, без
    него отдавали пустой список, а непроверенное по всем заданиям живёт на
    экране «Проверка по ученику».

    По умолчанию скрыты студенты, не заполнившие анкету (profile_completed=False).
    Суперадмин может раскрыть их через show_hidden=True.

    has_access_deadline=True — учёт пробного набора: показывает только тех,
    кому проставлен `access_until` (владелец 11.09.2026 — «28 сентября владелец
    открывает фильтр и видит всех, кому пора решать»). Вошедший по ссылке
    набора новичок анкету обычно ещё не заполнил, поэтому этот фильтр тоже
    снимает отсев по profile_completed — иначе он был бы не виден вовсе.

    archived=True — режим архива (`_can_read_archive`): вместо действующих
    учеников отдаются архивные (прошлые потоки), их данные открыты только на чтение.
    Куратору с личным доступом — весь архив школы, как ГП (владелец 30.09.2026).
    """
    hide_pre_cohort = not (show_hidden and user["role_rank"] >= 5) and not has_access_deadline

    if archived:
        if not _can_read_archive(user):
            return []
        student_role = db.query(Role).filter(Role.rank == 1).first()
        if not student_role:
            return []
        return (
            db.query(User)
            .filter(
                User.role_id == student_role.id,
                User.archived_at.isnot(None),
                User.deleted_at.is_(None),
                User.id.notin_(REPORT_EXCLUDED_USER_IDS),
            )
            .order_by(User.last_name, User.first_name)
            .all()
        )

    if user["role_rank"] < 4:
        # Куратор и преподаватель видят всех своих активных учеников, включая
        # тех, кто ещё не завершил онбординг (profile_completed=False) – анкету заполняет
        # сам ученик (персонал — не может), поэтому только-что привязанный
        # ученик иначе «пропадал» у куратора без сигнала. Незавершённые
        # помечаются бейджем needs_setup в сайдбаре.
        return (
            db.query(User)
            .filter(
                User.curator_id == user["user_id"],
                User.is_active == True,  # noqa: E712
                User.id.notin_(REPORT_EXCLUDED_USER_IDS),
            )
            .order_by(User.last_name, User.first_name)
            .all()
        )

    # rank >= 4: все активные ученики
    student_role = db.query(Role).filter(Role.rank == 1).first()
    if not student_role:
        return []

    q = db.query(User).filter(
        User.role_id == student_role.id,
        User.is_active == True,  # noqa: E712
        User.id.notin_(REPORT_EXCLUDED_USER_IDS),
    )
    if hide_pre_cohort:
        q = q.filter(User.profile_completed == True)  # noqa: E712
    if has_access_deadline:
        q = q.filter(User.access_until.isnot(None))

    return q.order_by(User.last_name, User.first_name).all()


def _parse_bool(s: str) -> bool:
    return s.lower() in ("1", "true", "yes", "on")


def _check_access(student_id: int, user: dict, db: DBSession, *, read_archive: bool = False) -> User:
    """read_archive=True открывает архивного ученика на чтение — тем, кому открыт
    архив (`_can_read_archive`), и только в GET-роутах панели. Куратора дальше
    всё равно держит «только свои» в `get_student_for_staff_access`. Мутации архива отсекаются сами: без этого
    флага архивный ученик не находится вовсе, значит POST/PATCH/DELETE отвечают 404."""
    return get_student_for_staff_access(
        db,
        user,
        student_id,
        active_only=True,
        allow_archived=read_archive and _can_read_archive(user),
        not_found_detail="Ученик не найден",
        forbidden_detail="Нет доступа к этому ученику",
    )



def _enrich(s: User, counts_by_user: dict, avg_by_user: dict,
            mock_counts_by_user: dict | None = None,
            unchecked_by_user: dict | None = None,
            scored_subjects_by_user: dict | None = None,
            has_case_by_user: dict | None = None,
            can_see_contacts: bool = True) -> dict:
    return {
        "id": s.id,
        "name": f"{s.last_name or ''} {s.first_name or s.name}".strip(),
        "photo_url": s.photo_url,
        "cohort_tag": s.cohort_tag,
        "tariff": s.tariff,
        "access_until": s.access_until,
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
        "tg_username": (s.tg_username or "").lstrip("@").lower() if can_see_contacts else "",
        "mock_count": mock_counts_by_user.get(s.id, 0) if mock_counts_by_user else 0,
        "unchecked": unchecked_by_user.get(s.id, 0) if unchecked_by_user else 0,
        "scored_subjects": scored_subjects_by_user.get(s.id, []) if scored_subjects_by_user else [],
        # Незавершённый онбординг: ученик не заполнил анкету.
        # Точно совпадает с набором, который скрывается от персонала (pre-cohort).
        "needs_setup": not s.profile_completed,
    }


# ── Main page ─────────────────────────────────────────────────────────────────

# Архив идёт отдельным путём (а не флагом на /students), чтобы подсветка пункта
# меню и возврат «к действующим» работали без разбора query-параметров.
# Путь именно /cabinet/archive: /cabinet/students/archive перехватывает
# legacy-редирект `/students/{student_id}` из cabinet_curator.py — его роутер
# подключён раньше, и «archive» уходит в int-параметр (422).
@router.get("/archive", response_class=HTMLResponse)
def students_archive_panel(
    request: Request,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
    student: int = Query(0),
    tab: str = Query("portfolio"),
):
    if not _can_read_archive(user):
        raise HTTPException(status_code=403, detail="Архив доступен только Главному преподавателю и суперадмину")
    return _render_students_panel(
        request, user, db, student=student, tab=tab, archived="1",
    )


@router.get("/students", response_class=HTMLResponse)
def students_panel(
    request: Request,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
    student: int = Query(0),
    tab: str = Query("portfolio"),
    show_hidden: str = Query(""),
    has_access_deadline: str = Query(""),
):
    return _render_students_panel(
        request, user, db,
        student=student,
        tab=tab,
        show_hidden=show_hidden,
        has_access_deadline=has_access_deadline,
    )


def _render_students_panel(
    request: Request,
    user: dict,
    db: DBSession,
    *,
    student: int = 0,
    tab: str = "portfolio",
    show_hidden: str = "",
    archived: str = "",
    has_access_deadline: str = "",
):
    is_admin_panel = user["role_rank"] >= 4
    show_hidden_b = user["role_rank"] >= 5 and _parse_bool(show_hidden)
    has_access_deadline_b = is_admin_panel and _parse_bool(has_access_deadline)
    # Архив прошлых потоков — только на чтение, кому открыт (`_can_read_archive`).
    archived_b = _can_read_archive(user) and _parse_bool(archived)

    students = _get_accessible_students(
        user, db,
        show_hidden=show_hidden_b,
        archived=archived_b,
        has_access_deadline=has_access_deadline_b,
    )

    active_hard_filters: list[dict] = []
    if show_hidden_b:
        active_hard_filters.append({"key": "show_hidden", "label": "Включая не заполнивших анкету"})
    if has_access_deadline_b:
        active_hard_filters.append({"key": "has_access_deadline", "label": "Со сроком доступа"})

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

    can_score = role_can_score(user["role_rank"]) and not archived_b
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
            can_see_contacts=is_admin_panel,
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
    if user["role_rank"] == 2 and students and not archived_b:
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
    if archived_b:
        sidebar_title = "Архив учеников"
    valid_tabs = ("portfolio", "tasks", "mock-exams", "statistics")
    # «Цикл пробника» слит с «Пробниками» 29.09.2026: старые закладки и
    # уведомления с `tab=cycles` открывают то же самое, а не «Портфолио».
    if tab == "cycles":
        tab = "mock-exams"
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
    has_missing_enrollment_year = any(not s.enrollment_year for s in students)

    return templates.TemplateResponse(request, "cabinet_students.html", {
        "request": request,
        "user": user,
        "sidebar_students": sidebar_students,
        "initial_student_id": student,
        "initial_tab": tab if tab in valid_tabs else "portfolio",
        "nav_active": (
            "archive" if archived_b
            else "statistics" if (tab in valid_tabs and tab == "statistics")
            else "students"
        ),
        "can_score": can_score,
        "sidebar_title": sidebar_title,
        "mock_subjects": MOCK_SUBJECTS,
        "months": MONTHS,
        "tariffs": TARIFFS_CURRENT,
        # Подписи для JS карточки: фильтра `tariff_label` в скрипте нет.
        "tariff_labels": TARIFF_DISPLAY,
        "current_year": datetime.now(timezone.utc).year,
        "show_curator_filter": show_curator_filter,
        "curators": curators,
        "enrollment_years": enrollment_years,
        "has_missing_enrollment_year": has_missing_enrollment_year,
        "active_hard_filters": active_hard_filters,
        "is_admin_panel": is_admin_panel,
        "is_superadmin": user["role_rank"] >= 5,
        "is_archive_view": archived_b,
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
    student = _check_access(student_id, user, db, read_archive=True)
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
    portfolio_count = portfolio_item_count(db, student_id)
    mock_works = [w for w in works if w.work_type == WORK_TYPE_MOCK_EXAM]
    scored = [w for w in mock_works if w.score is not None]
    avg_score = round(sum(float(w.score) for w in scored) / len(scored)) if scored else None
    legacy_photo_count = (
        db.query(func.count(LegacyPortfolioPhoto.id))
        .filter(LegacyPortfolioPhoto.user_id == student_id)
        .scalar()
    ) or 0

    # Контакты ученика (телефон/телефон родителя/Telegram) видны только рангам >= 4
    # (админ/суперадмин) — куратор (rank=2) их больше не получает в ответе.
    can_see_contacts = user["role_rank"] >= 4

    # «Учёба сейчас» (владелец 29.09.2026): карточка показывает то, с чем ученик
    # работает в ленте, а проверка остаётся на одном экране (правило 12).
    # Счётчик — по всем доменам экрана проверки и за всё время, поэтому ссылка
    # ведёт на неделю самой старой непроверенной сдачи: экран показывает одну
    # неделю, и без неё «Не проверено: 3» открыл бы пустую текущую.
    pending = [i for i in _review_items_all_time(db, user, student_id) if not i.is_reviewed]
    oldest = min((i.submitted_at for i in pending if i.submitted_at), default=None)
    study_now = {
        "unreviewed": len(pending),
        "review_week": msk_input_value(oldest)[:10] if oldest else "",
    }
    # Точка А — только ГП и суперадмину: её экран закрыт `require_admin_role`.
    if user["role_rank"] >= 4:
        point_a = student_point_a(db, student, with_images=False)
        study_now["point_a"] = {
            "has_plates": bool(point_a.plates),
            "average": point_a.average,
            "is_done": point_a.is_done,
        }

    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "first_name": student.first_name,
            "last_name": student.last_name,
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
            "phone": student.phone if can_see_contacts else None,
            "parent_phone": student.parent_phone if can_see_contacts else None,
            "parent_name": student.parent_name if can_see_contacts else None,
            "tg_username": student.tg_username if can_see_contacts else None,
            "email": student.email if can_see_contacts else None,
            "birth_date": (
                student.birth_date.strftime("%d.%m.%Y")
                if can_see_contacts and student.birth_date else None
            ),
            "city": student.city if can_see_contacts else None,
            "timezone": (
                TIMEZONE_DISPLAY.get(student.timezone, student.timezone)
                if can_see_contacts else None
            ),
            "sdek_address": student.sdek_address if can_see_contacts else None,
            "can_see_contacts": can_see_contacts,
            "about": student.about,
            "about_html": format_rich_text(student.about) if student.about else None,
            "tariff": student.tariff or "—",
            "past_tariffs": student.past_tariffs,
            "study_mode": student.study_mode,
            "has_case": has_case_growth(works),
            "course_periods": student.course_periods,
            "lessons_count": student.lessons_count,
            "enrollment_year": student.enrollment_year,
            "university_year": student.university_year,
            # Срок доступа для формы карточки: `datetime-local` понимает только
            # местное время без таймзоны, поэтому отдаём московское — в том же
            # виде, в каком его вводят обратно (`parse_msk_local`).
            "access_until": msk_input_value(student.access_until),
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "profile_completed": student.profile_completed,
            "curator_name": curator_name,
            "avg_score": avg_score,
            "avg_score_by_subject": avg_score_by_subject_all_time(db, student_id),
            "portfolio_count": portfolio_count,
            "mock_exam_count": len(mock_works),
            "legacy_photo_count": legacy_photo_count,
            "study_now": study_now,
        },
    })


# ── AJAX: задания из ленты ────────────────────────────────────────────────────

# Что ученик делает в ленте `/cabinet/learning`: ответы на блоки, работы, сданные
# внутри задания, и домашка старого образца. Пробник (`work`, `exam_cycle`) сюда
# не входит — у него своя вкладка «Пробники», второй его копии здесь не нужно.
_TASK_DOMAINS = (DOMAIN_TASK_BLOCK, DOMAIN_BLOCK_WORK, DOMAIN_HOMEWORK)


def _review_items_all_time(db: DBSession, user: dict, student_id: int) -> list:
    """Сдачи ученика по всем доменам экрана проверки, без недельного окна.

    Та же функция, что кормит `/cabinet/staff/students-review/{id}` (правило 12),
    с той же областью видимости куратора — своей выборки карточка не держит."""
    return student_review_items(
        db,
        student_id=student_id,
        curator_id=None if user["role_rank"] >= FULL_ACCESS_RANK else user["user_id"],
        role_rank=user["role_rank"],
    )


def _task_item_json(item) -> dict:
    submitted = item.submitted_at
    return {
        "domain": item.domain,
        "id": item.item_id,
        "title": item.title,
        "subject": item.subject,
        "date_label": msk_input_value(submitted)[:10] if submitted else "",
        "is_reviewed": item.is_reviewed,
        "needs_revision": item.needs_revision,
        "question": item.question,
        "chosen": item.chosen or [],
        "text": item.text,
        "images": item.images or [],
        "review_comment": item.review_comment,
        # У ответа на блок своего экрана нет — его проверяют на экране
        # «Проверка по ученику», на неделе сдачи.
        "review_url": item.review_url if item.domain != DOMAIN_TASK_BLOCK else "",
    }


@router.get("/students/{student_id}/tasks")
def get_student_tasks(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Вкладка «Задания»: только чтение, ссылки ведут на существующие экраны
    проверки. Своей очереди и своих кнопок «проверено» у вкладки нет."""
    student = _check_access(student_id, user, db, read_archive=True)
    enrolled_at = student.enrolled_at or student.created_at
    items = [
        i for i in _review_items_all_time(db, user, student_id)
        if i.domain in _TASK_DOMAINS
    ]
    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "avg_score_by_subject": avg_score_by_subject_all_time(db, student_id),
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
        },
        "items": [_task_item_json(i) for i in items],
    })


# ── AJAX: portfolio ───────────────────────────────────────────────────────────

@router.get("/students/{student_id}/portfolio")
def get_portfolio(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_access(student_id, user, db, read_archive=True)
    enrolled_at = student.enrolled_at or student.created_at

    before_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_BEFORE, Work.status == "success")
        .order_by(Work.created_at.desc()).limit(100).all()
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
            "avg_score_by_subject": avg_score_by_subject_all_time(db, student_id),
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
        },
        # «До» — плоский список без месяцев (владелец 09.09.2026). Стартовый
        # набор ученик грузит один раз в предобучении: месяцы там ничего не
        # разделяют, а папка месяца тянула за собой массовое удаление.
        # `thumb_url` — превью для квадратика (шаг 5 плана 2026-09-29-apparchi-
        # students-phone): есть только у работ с 29.09.2026, у сдач в заданиях
        # его нет вовсе, поэтому `getattr`. Пусто — экран берёт `s3_url`.
        "before_flat": [
            {"s3_url": w.s3_url, "thumb_url": w.thumb_s3_url, "filename": w.filename,
             "id": w.id, "source": "work"}
            for w in before_works
        ],
        # «После» — работы портфолио вместе со сдачами внутри заданий
        # (владелец 09.09.2026), сборка в `services/portfolio.py`.
        # `work_total` — сколько в месяце настоящих `Work`: по нему считается
        # массовое удаление папки, сдачи по заданиям тот роут не трогает.
        "after_by_month": [
            {
                "month": g["month"], "year": g["year"], "total": g["total"],
                "work_total": g["work_total"],
                "works": [
                    {
                        "s3_url": w.s3_url, "thumb_url": getattr(w, "thumb_s3_url", None),
                        "filename": w.filename, "id": w.id, "source": item_source(w),
                    }
                    for w in g["works"]
                ],
            }
            for g in after_gallery_groups(db, student_id)
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
):
    student = _check_access(student_id, user, db, read_archive=True)
    enrolled_at = student.enrolled_at or student.created_at

    # Фильтра «только за текущий период» нет с 29.09.2026 (владелец): окно
    # `FeaturePeriod` закрывает сдачу пробника у ученика, но к тому, что
    # преподаватель видит в карточке, отношения не имеет.
    mock_works = (
        db.query(Work)
        .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_MOCK_EXAM, Work.status == "success")
        .order_by(Work.created_at.desc())
        .limit(100)
        .all()
    )
    scored = [w for w in mock_works if w.score is not None]
    avg_score = round(sum(float(w.score) for w in scored) / len(scored)) if scored else None

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

    # Нехватка этапных (владелец 02.10.2026): финал приняли, а ГП должен
    # видеть, что этапных меньше заданного.
    from app.services.exam_cycle import stage_photo_shortfalls
    shortfalls = stage_photo_shortfalls(db, [w.cycle_id for w in mock_works])

    def serialize_mock_work(w: Work) -> dict:
        created_at = w.created_at
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        local_dt = created_at.astimezone(MSK_TZ) if created_at else None
        return {
            "id": w.id,
            "s3_url": w.s3_url,
            "thumb_url": w.thumb_s3_url,
            "filename": w.filename,
            "score": float(w.score) if w.score is not None else None,
            "comment": w.comment,
            "comment_html": format_rich_text(w.comment) if w.comment else None,
            "created_at": created_at.isoformat() if created_at else None,
            "work_date": local_dt.date().isoformat() if local_dt else "",
            "date_label": local_dt.strftime("%d.%m.%Y") if local_dt else "",
            "cycle_id": w.cycle_id,
            "has_feedback": w.id in fb_work_ids,
            "stage_shortfall": shortfalls.get(w.cycle_id),
        }

    locks = {
        lock.subject: {"is_locked": lock.is_locked}
        for lock in db.query(MockExamLock).filter(MockExamLock.user_id == student_id).all()
    }

    # Архив (импорт из Telegram-чат-бота, задним числом): read-only, по месяцам.
    # Намеренно не смешивается с mock_works — там id ссылается на реальный Work
    # и участвует в оценке/пересдаче/разблокировке; архивные фото этого не имеют.
    legacy_photos = (
        db.query(LegacyPortfolioPhoto)
        .filter(LegacyPortfolioPhoto.user_id == student_id)
        .order_by(LegacyPortfolioPhoto.sent_at.desc())
        .limit(2000)
        .all()
    )
    legacy_groups: dict[tuple, list] = defaultdict(list)
    for p in legacy_photos:
        legacy_groups[(p.year, p.month)].append(p)
    legacy_by_month = [
        {
            "year": year,
            "month": month,
            "total": len(items),
            "photos": [{"s3_url": p.s3_url, "filename": p.original_filename} for p in items],
        }
        for (year, month), items in sorted(
            legacy_groups.items(),
            key=lambda kv: (kv[0][0], MONTH_TO_NUM.get(kv[0][1], 99)),
            reverse=True,
        )
    ]

    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "avg_score": avg_score,
            "avg_score_by_subject": avg_score_by_subject_all_time(db, student_id),
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
        },
        "mock_works": {
            subject: [serialize_mock_work(w) for w in works_list]
            for subject, works_list in works_by_subject.items()
        },
        "mock_locks": locks,
        "legacy_by_month": legacy_by_month,
    })


# ── AJAX: statistics (динамика баллов по пробникам) ──────────────────────────

@router.get("/students/{student_id}/statistics")
def get_statistics(
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    from app.services.stats import student_score_curve

    student = _check_access(student_id, user, db, read_archive=True)
    enrolled_at = student.enrolled_at or student.created_at
    return JSONResponse({
        "student": {
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "tariff": student.tariff or "—",
            "study_duration": study_duration_text(enrolled_at) if enrolled_at else None,
            "avg_score_by_subject": avg_score_by_subject_all_time(db, student_id),
            "photo_url": student.photo_url,
            "cohort_tag": student.cohort_tag,
        },
        "points": student_score_curve(db, student_id),
    })


# ── POST: оценить работу ──────────────────────────────────────────────────────

@router.post("/students/{student_id}/works/{work_id}/score")
def score_work(
    student_id: int,
    work_id: int,
    user: Annotated[dict, Depends(require_scorer)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    background_tasks: BackgroundTasks,
    score: float = Form(...),
    comment: str = Form(""),
    tab: str = Form("mock-exams"),
):
    """Балл ставит только Главный преподаватель и выше (`rbac.SCORE_MIN_RANK`,
    владелец 30.09.2026). С 01.09 по 30.09.2026 балл ставил и куратор — он
    теперь даёт обратную связь и видит балл ГП.

    Адрес общий: им же пользуются «Проверка пробников» и «Проверка отработок»
    ГП, поэтому закрыт рангом, а не удалён. `_check_access` закрывает
    архивного и удалённого ученика на запись. Модератор сюда не доходит: его
    запись отсекает белый список `rbac.py`.
    """
    _check_access(student_id, user, db)
    work = db.query(Work).filter(Work.id == work_id, Work.user_id == student_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if tab not in ("portfolio", "mock-exams"):
        tab = "mock-exams"

    if not (0 <= score <= 100):
        raise HTTPException(status_code=422, detail="Балл должен быть от 0 до 100")
    work.score = int(round(score))
    work.comment = (comment.strip() or None)
    if work.comment and len(work.comment) > 500:
        work.comment = work.comment[:500]
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
    return RedirectResponse(
        f"/cabinet/students?student={student_id}&tab={tab}&saved=1", status_code=302
    )


# ── POST: вернуть пробник на доработку (только разблокировка, без оценки) ────

@router.post("/students/{student_id}/mock-exams/{work_id}/revision")
def send_mock_exam_to_revision(
    student_id: int,
    work_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    background_tasks: BackgroundTasks,
):
    if user["role_rank"] < 4:
        raise HTTPException(status_code=403, detail="Доступно только админу и суперадмину")
    _check_access(student_id, user, db)

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
    work.needs_revision_at = datetime.now(timezone.utc)

    subject = work.subject
    # Возвращаем именно попытку исходного билета. Без этого при нескольких
    # вариантах в пробнике «Начать пробник» мог случайно выдать другой билет,
    # а догруженные этапы попадали в новый цикл.
    if work.cycle_id is not None:
        cycle = db.query(ExamCycle).filter(ExamCycle.id == work.cycle_id).first()
        if cycle is not None and cycle.ticket_id is not None:
            subject = cycle.subject or subject
            attempt = (
                db.query(MockExamAttempt)
                .filter(
                    MockExamAttempt.user_id == student_id,
                    MockExamAttempt.subject == subject,
                    MockExamAttempt.ticket_id == cycle.ticket_id,
                )
                .order_by(MockExamAttempt.started_at.desc(), MockExamAttempt.id.desc())
                .first()
            )
            if attempt is not None:
                attempt.completed_at = None
                attempt.expired_at = None
                attempt.started_at = datetime.now(timezone.utc)
            else:
                ticket = db.get(ExamTicket, cycle.ticket_id)
                if ticket is not None:
                    db.add(MockExamAttempt(
                        user_id=student_id,
                        subject=subject,
                        ticket_id=ticket.id,
                        ticket_title=ticket.title,
                        ticket_description=ticket.description,
                        ticket_image_url=ticket.image_s3_url,
                    ))

    if subject and subject in MOCK_SUBJECTS:
        lock = db.query(MockExamLock).filter(
            MockExamLock.user_id == student_id,
            MockExamLock.subject == subject,
        ).first()
        if lock:
            lock.is_locked = False
            lock.unlocked_at = datetime.now(timezone.utc)
            lock.unlocked_by_id = user["user_id"]

    notification = Notification(
        user_id=student_id,
        title="Пробник возвращён на доработку",
        text="Догрузи этапные фото выполненного задания и при необходимости обнови финальное фото.",
    )
    db.add(notification)
    db.commit()
    background_tasks.add_task(notify, notification.id)
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
    _check_access(student_id, user, db)

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
    access_until: str = Form(""),
):
    student = _check_access(student_id, user, db)

    errors = []
    first_name = first_name.strip()
    last_name = last_name.strip()
    phone = phone.strip()
    parent_phone = parent_phone.strip()
    tg_username = tg_username.strip().lstrip("@")
    tariff = tariff.strip().upper()
    clear_tariff = tariff == "__NONE__"
    if clear_tariff:
        tariff = ""
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

    # Срок доступа: пусто — снять ограничение (так оплативший возвращается к
    # обучению), дата — закрыть кабинет в этот момент по Москве. Мусор в поле
    # отличаем от пустого явно: `parse_msk_local` на оба случая отвечает None,
    # и молчаливое «не разобрали — значит сняли» открыло бы доступ тому, кому
    # его как раз ограничивают.
    access_until_raw = access_until.strip()
    parsed_access_until = parse_msk_local(access_until_raw) if access_until_raw else None
    if access_until_raw and parsed_access_until is None:
        errors.append("Неверная дата срока доступа")

    parsed_enrollment_year = None
    if enrollment_year.strip():
        try:
            parsed_enrollment_year = int(enrollment_year.strip())
            if not (2000 <= parsed_enrollment_year <= 2100):
                errors.append("Начало обучения – год от 2000 до 2100")
        except ValueError:
            errors.append("Начало обучения – год цифрами, например 2025")

    parsed_university_year = None
    if university_year.strip():
        try:
            parsed_university_year = int(university_year.strip())
            if not (2000 <= parsed_university_year <= 2100):
                errors.append("Год поступления в вуз – от 2000 до 2100")
        except ValueError:
            errors.append("Год поступления в вуз – цифрами, например 2026")

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
    # Каким поле «Доступ до» пришло к куратору — чтобы ниже отличить
    # предзаполненную дату от вписанной руками.
    access_until_prefilled = msk_input_value(student.access_until)
    tariff_cleared_access = False
    if tariff or clear_tariff:
        tariff_cleared_access = (
            tariff_change_clears_access(student.tariff, tariff)
            and student.access_until is not None
        )
        apply_tariff_change(db, user["user_id"], student, tariff)
    if parsed_enrollment_year is not None:
        student.enrollment_year = parsed_enrollment_year
    if parsed_university_year is not None:
        student.university_year = parsed_university_year
    student.cohort_tag = cohort_tag or None
    # Срок появился у человека с незаполненной анкетой — это новичок пробного
    # набора, которого куратор пометил руками (тот, кто вошёл напрямую с
    # apparchi.ru, минуя ссылку `/proba`). Тариф ему снимаем по тому же
    # правилу, что и на входе по ссылке: анкета шаг «Тариф обучения» ему уже не
    # покажет, и без этой строки он молча остался бы на «УВЕРЕННЫЙ» из дефолта
    # при создании аккаунта. Заполненную анкету не трогаем: там тариф человек
    # выбрал сам, а срок куратор мог поставить оплатившему по своей причине.
    # Явно выбранный в этой же форме тариф выигрывает — он записан выше.
    if (
        parsed_access_until is not None
        and student.access_until is None
        and not student.profile_completed
        and not tariff
    ):
        student.tariff = ""
    # Поле «Доступ до» в карточке предзаполнено текущей датой ученика, поэтому
    # форма всегда присылает её обратно. Смену тарифа с «новенького» нельзя
    # оставлять наедине с этим полем: прилетевшая дата вернула бы только что
    # снятый срок, и владелец видел бы блокировку после смены тарифа (прод,
    # 28.09.2026: ученице проставили «Я С ВАМИ», кабинет остался закрытым).
    #
    # Обнуляем только нетронутое поле — то, которое пришло ровно таким, каким
    # его отрисовали. Дата, вписанная в этой же форме руками, главнее правила:
    # она значит «оплатил, доступ до такого-то числа», и молча её терять нельзя.
    if tariff_cleared_access and access_until_raw == access_until_prefilled:
        parsed_access_until = None
    student.access_until = parsed_access_until
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
MAX_FILES = 20

WORK_TYPE_LABELS = {
    "before": "До", "after": "После",
    "mock_exam": "Пробник",
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

    # Отработки нет с 29.09.2026 (владелец): у ученика к ней нет входа, и
    # загрузка за него создавала бы работы, которых он не увидит как задачу.
    # Проверки «У ученика нет VK ID» тоже нет: колонка обязательная, у
    # Telegram-учеников в ней служебный номер, путь в S3 по-прежнему строится
    # от него.
    valid_types = {WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM}
    if work_type not in valid_types:
        return JSONResponse({"ok": False, "error": "Неверный тип работы"}, status_code=400)
    if not student.tariff:
        return JSONResponse({"ok": False, "error": "У ученика не указан тариф"}, status_code=400)

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
        photo_bytes = await photo.read(MAX_SIZE + 1)
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
        return s3_service.s3_path_after(vk_id, tariff, filename)

    success_count = 0
    fail_count = 0
    loop = asyncio.get_running_loop()

    def _compress_and_upload_s3(raw: bytes, path: str):
        compressed = compress_image(raw)
        url = s3_service.upload_to_s3(path, compressed, "image/jpeg")
        thumb_url = upload_work_thumb(path, compressed) if url else None
        return url, thumb_url

    # Цикл Пробника: получить/создать для пробника
    cycle_id: int | None = None
    attempt_no: int | None = None
    if work_type == WORK_TYPE_MOCK_EXAM and subject:
        from app.services import exam_cycle as cycle_service
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
            s3_url, thumb_url = await loop.run_in_executor(
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
                thumb_s3_url=thumb_url,
                subject=subject if work_type == WORK_TYPE_MOCK_EXAM else None,
                tariff=tariff,
                score=work_score,
                scored_at=datetime.now(timezone.utc) if work_score is not None else None,
                scored_by_id=user["user_id"] if work_score is not None else None,
                status="success",
                drive_status="s3_only",
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


def _work_has_feedback_response(exc: WorkHasFeedbackError) -> JSONResponse:
    """Отказ удаления работы с обратной связью. Текст в обоих ключах: экран
    «Ученики» читает `error`, экраны проверки пробника и пересдачи — `detail`."""
    return JSONResponse(
        {"ok": False, "error": str(exc), "detail": str(exc)}, status_code=409
    )


# ── DELETE: удалить "папку" (все работы за месяц/тип) ────────────────────────

@router.delete("/students/{student_id}/works/bulk")
async def bulk_delete_works(
    student_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
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

    try:
        deleted_count = _delete_work_rows_with_dependents(db, works)
    except WorkHasFeedbackError as exc:
        return _work_has_feedback_response(exc)

    db.commit()
    return JSONResponse({"ok": True, "deleted_count": deleted_count})


# ── DELETE: удалить работу (фото) ученика ────────────────────────────────────

@router.patch("/students/{student_id}/portfolio/month")
async def rename_portfolio_month(
    student_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
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
    _csrf: Annotated[None, Depends(require_csrf_header)],
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
    _csrf: Annotated[None, Depends(require_csrf_header)],
    db: Annotated[DBSession, Depends(get_db)],
):
    _check_access(student_id, user, db)
    work = db.query(Work).filter(Work.id == work_id, Work.user_id == student_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")

    try:
        _delete_work_rows_with_dependents(db, [work])
    except WorkHasFeedbackError as exc:
        return _work_has_feedback_response(exc)
    db.commit()
    return JSONResponse({"ok": True})
