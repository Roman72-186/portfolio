import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone, date
from typing import Annotated

import bcrypt as _bcrypt_lib
from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as DBSession, aliased

from app.config import settings
from app.constants import MOCK_SUBJECTS, FEATURE_LABELS, FEATURE_PORTFOLIO_UPLOAD, FEATURE_MOCK_EXAM, FEATURE_RETAKE, TARIFFS
from app.db.database import get_db
from app.dependencies import require_superadmin, require_admin_role, require_csrf
from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.feature_period import FeaturePeriod
from app.services.feature_periods import invalidate_feature_cache, get_active_period
from app.services.tz import today_msk, msk_midnight
from app.models.role import Role
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services import s3 as s3_service
from app.services.auth_links import issue_one_time_login_link
from app.tmpl import templates

_TRANSLIT = str.maketrans(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
    "abvgdeejzijklmnoprstufhccssxyxeuaABVGDEEJZIJKLMNOPRSTUFHCCSSXYXEUA",
)
_PWD_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


def _transliterate(s: str) -> str:
    return s.translate(_TRANSLIT).lower()


def _gen_password(length: int = 10) -> str:
    return "".join(secrets.choice(_PWD_CHARS) for _ in range(length))


def _hash_password(plain: str) -> str:
    return _bcrypt_lib.hashpw(plain.encode(), _bcrypt_lib.gensalt()).decode()


def _make_login(user: User, db: DBSession) -> str:
    """Generate a unique staff_login like 'ivan.s' based on first/last name."""
    first = _transliterate(user.first_name or user.name or "user")
    last_initial = _transliterate((user.last_name or "")[:1])
    base = f"{first}.{last_initial}" if last_initial else first
    # Keep only safe ascii chars
    base = "".join(c for c in base if c in string.ascii_lowercase + string.digits + ".")
    base = base[:20] or "user"

    candidate = base
    suffix = 2
    while db.query(User).filter(User.staff_login == candidate, User.id != user.id).first():
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate

router = APIRouter(prefix="/cabinet")


def _month_name_prep(month: int) -> str:
    names = ["", "январе", "феврале", "марте", "апреле", "мае", "июне",
             "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]
    return names[month] if 1 <= month <= 12 else ""


def _load_dashboard_data(db: DBSession, now: datetime) -> dict:
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    # ── Users by role ─────────────────────────────────────────────────────────
    role_rows = (
        db.query(Role.display_name, Role.rank, func.count(User.id).label("cnt"))
        .outerjoin(User, (User.role_id == Role.id) & (User.is_active == True))
        .filter(Role.name != "модератор")
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

    # ── Works ─────────────────────────────────────────────────────────────────
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

    # ── Scores ────────────────────────────────────────────────────────────────
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

    # ── Curators (rank 2) ─────────────────────────────────────────────────────
    # Limit: max 100 curators (защита от медленной выборки)
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

    # ── Admins (rank 4) ───────────────────────────────────────────────────────
    # Limit: max 50 admins (защита от медленной выборки)
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

    # ── Recent uploads ────────────────────────────────────────────────────────
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
        }
        for w, u in recent_rows
    ]

    # ── Feature periods status ────────────────────────────────────────────────
    today = today_msk()
    active_features = set(
        row[0]
        for row in db.query(FeaturePeriod.feature)
        .filter(
            FeaturePeriod.is_active.is_(True),
            FeaturePeriod.start_date <= today,
            FeaturePeriod.end_date >= today,
        )
        .all()
    )
    feature_statuses = {
        feat: {"label": FEATURE_LABELS[feat], "open": feat in active_features}
        for feat in ALL_FEATURES
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


@router.get("/superadmin", response_class=HTMLResponse)
def cabinet_superadmin(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
):
    now = datetime.now(timezone.utc)
    ctx = _load_dashboard_data(db, now)
    ctx.update({"request": request, "user": user})
    return templates.TemplateResponse("cabinet_staff.html", ctx)


@router.post("/superadmin/set-credentials", response_class=HTMLResponse)
def superadmin_set_credentials(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    target_user_id: int = Form(...),
):
    target = db.query(User).filter(User.id == target_user_id, User.is_active == True).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Keep existing login or generate new one
    if not target.staff_login:
        target.staff_login = _make_login(target, db)

    new_password = _gen_password()
    target.password_hash = _hash_password(new_password)
    db.commit()

    now = datetime.now(timezone.utc)
    ctx = _load_dashboard_data(db, now)
    staff_url = f"https://{settings.domain}/login" if settings.domain else "/login"
    ctx.update({
        "request": request,
        "user": user,
        "issued_creds": {
            "name": f"{target.last_name or ''} {target.first_name or target.name}".strip(),
            "login": target.staff_login,
            "password": new_password,
            "url": staff_url,
        },
    })
    return templates.TemplateResponse("cabinet_staff.html", ctx)


@router.post("/superadmin/issue-link", response_class=HTMLResponse)
def superadmin_issue_link(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    target_user_id: int = Form(...),
):
    target = db.query(User).filter(User.id == target_user_id, User.is_active == True).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    base_url = f"https://{settings.domain}" if settings.domain else str(request.base_url).rstrip("/")
    issued_link, login_token = issue_one_time_login_link(
        db,
        user=target,
        base_url=base_url,
        issued_by=f"superadmin:{user['user_id']}",
    )

    now = datetime.now(timezone.utc)
    ctx = _load_dashboard_data(db, now)
    ctx.update({
        "request": request,
        "user": user,
        "issued_link": issued_link,
        "issued_link_name": f"{target.last_name or ''} {target.first_name or target.name}".strip(),
        "issued_link_expires_at": login_token.expires_at,
    })
    return templates.TemplateResponse("cabinet_staff.html", ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# Exam Assignments — создание заданий для сдачи пробников
# ═══════════════════════════════════════════════════════════════════════════════

def _ticket_s3_path(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    key = uuid.uuid4().hex[:12]
    return f"Экзамены/билеты/{key}.{ext}"


# ── Редирект старых URL (backward compat) ────────────────────────────────────

@router.get("/superadmin/exam-assignments")
def _exam_assignments_compat(_user: Annotated[dict, Depends(require_admin_role)]):
    return RedirectResponse("/cabinet/exam-assignments", status_code=301)

@router.get("/superadmin/exam-assignments/create")
def _exam_assignment_create_compat(_user: Annotated[dict, Depends(require_admin_role)]):
    return RedirectResponse("/cabinet/exam-assignments/create", status_code=301)

@router.get("/superadmin/exam-assignments/{assignment_id}")
def _exam_assignment_detail_compat(assignment_id: int, _user: Annotated[dict, Depends(require_admin_role)]):
    return RedirectResponse(f"/cabinet/exam-assignments/{assignment_id}", status_code=301)


# ── Список заданий ───────────────────────────────────────────────────────────

@router.get("/exam-assignments", response_class=HTMLResponse)
def exam_assignments_list(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    assignments = (
        db.query(ExamAssignment)
        .order_by(ExamAssignment.created_at.desc())
        .limit(200)
        .all()
    )
    ticket_counts: dict[int, int] = {}
    if assignments:
        rows = (
            db.query(ExamTicket.assignment_id, func.count(ExamTicket.id))
            .filter(ExamTicket.assignment_id.in_([a.id for a in assignments]))
            .group_by(ExamTicket.assignment_id)
            .all()
        )
        ticket_counts = {aid: cnt for aid, cnt in rows}

    return templates.TemplateResponse("superadmin_exam_assignments.html", {
        "request": request,
        "user": user,
        "assignments": assignments,
        "ticket_counts": ticket_counts,
    })


# ── Форма создания ───────────────────────────────────────────────────────────

def _load_student_list(db: DBSession) -> list[dict]:
    student_role = db.query(Role).filter(Role.rank == 1).first()
    if not student_role:
        return []
    students = (
        db.query(User.id, User.first_name, User.last_name, User.name)
        .filter(User.role_id == student_role.id, User.is_active == True)
        .order_by(User.last_name, User.first_name)
        .all()
    )
    return [
        {"id": s.id, "name": f"{s.last_name or ''} {s.first_name or s.name}".strip()}
        for s in students
    ]


@router.get("/exam-assignments/create", response_class=HTMLResponse)
def exam_assignment_create_form(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    return templates.TemplateResponse("superadmin_exam_assignment_form.html", {
        "request": request,
        "user": user,
        "subjects": MOCK_SUBJECTS,
        "student_list": _load_student_list(db),
        "is_edit": False,
    })


@router.get("/exam-assignments/{assignment_id}/edit", response_class=HTMLResponse)
def exam_assignment_edit_form(
    assignment_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    assignment = db.query(ExamAssignment).filter(ExamAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    tickets = (
        db.query(ExamTicket)
        .filter(ExamTicket.assignment_id == assignment_id)
        .order_by(ExamTicket.ticket_number)
        .all()
    )

    # Load assignees per ticket
    assignees_by_ticket: dict[int, list[int]] = {t.id: [] for t in tickets}
    if tickets:
        rows = (
            db.query(ExamTicketAssignee.ticket_id, ExamTicketAssignee.user_id)
            .filter(ExamTicketAssignee.ticket_id.in_([t.id for t in tickets]))
            .all()
        )
        for ticket_id, user_id in rows:
            assignees_by_ticket.setdefault(ticket_id, []).append(user_id)

    existing_tickets = [
        {
            "title": t.title,
            "description": t.description or "",
            "image_url": t.image_s3_url or "",
            "image_path": t.image_s3_path or "",
            "start_date": t.start_date.isoformat() if t.start_date else "",
            "end_date": t.end_date.isoformat() if t.end_date else "",
            "assign_to_all": bool(t.assign_to_all),
            "student_ids": assignees_by_ticket.get(t.id, []),
        }
        for t in tickets
    ]

    import json as _json
    return templates.TemplateResponse("superadmin_exam_assignment_form.html", {
        "request": request,
        "user": user,
        "subjects": MOCK_SUBJECTS,
        "student_list": _load_student_list(db),
        "is_edit": True,
        "assignment": assignment,
        "existing_tickets_json": _json.dumps(existing_tickets, ensure_ascii=False),
    })


# ── AJAX загрузка фото билета ────────────────────────────────────────────────

@router.post("/upload-ticket-image")
async def upload_ticket_image(
    user: Annotated[dict, Depends(require_admin_role)],
    _csrf: Annotated[None, Depends(require_csrf)],
    file: UploadFile = File(...),
):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс. 10 МБ)")
    s3_path = _ticket_s3_path(file.filename or "image.jpg")
    url = s3_service.upload_to_s3(s3_path, data, file.content_type or "image/jpeg")
    return JSONResponse({"url": url, "path": s3_path if url else None})


# ── Сохранение задания ───────────────────────────────────────────────────────

@router.post("/exam-assignments/create")
async def exam_assignment_create_submit(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    form = await request.form()

    title = str(form.get("title", "")).strip()
    subject = str(form.get("subject", "")).strip()
    ticket_count = int(form.get("ticket_count", 1) or 1)
    ticket_count = max(1, min(10, ticket_count))

    if not title:
        raise HTTPException(status_code=422, detail="Название задания обязательно")
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=422, detail="Неверный предмет")

    assignment = ExamAssignment(
        title=title,
        subject=subject,
        created_by_id=user["user_id"],
        status="published",
    )
    db.add(assignment)
    db.flush()

    _build_tickets_from_form(db, assignment, form, ticket_count)
    _ensure_mock_exam_period_open(db, user["user_id"])
    db.commit()
    return RedirectResponse(
        f"/cabinet/exam-assignments/{assignment.id}", status_code=303
    )


def _ensure_mock_exam_period_open(db: DBSession, created_by_id: int) -> None:
    """Гарантирует наличие активного FeaturePeriod для пробников от сегодня
    до самой поздней end_date билетов в БД. Если такого нет — создаёт.

    Решает usability-проблему: админ создал билет, но забыл открыть период
    в /cabinet/periods → ученики ничего не видели.
    """
    today = today_msk()
    max_end_row = (
        db.query(func.max(ExamTicket.end_date))
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(ExamAssignment.status == "published", ExamTicket.end_date >= today)
        .first()
    )
    max_end = max_end_row[0] if max_end_row else None
    if not max_end:
        return  # нечего открывать

    # Уже есть активный период, покрывающий today?
    existing = (
        db.query(FeaturePeriod)
        .filter(
            FeaturePeriod.feature == FEATURE_MOCK_EXAM,
            FeaturePeriod.is_active.is_(True),
            FeaturePeriod.start_date <= today,
            FeaturePeriod.end_date >= today,
        )
        .first()
    )
    if existing:
        # Если max_end позднее текущего end_date — расширяем
        if existing.end_date < max_end:
            existing.end_date = max_end
            db.flush()
            invalidate_feature_cache(FEATURE_MOCK_EXAM)
        return

    # Создаём новый период
    db.add(FeaturePeriod(
        feature=FEATURE_MOCK_EXAM,
        title="Авто-открыто при публикации билета",
        start_date=today,
        end_date=max_end,
        is_active=True,
        created_by_id=created_by_id,
    ))
    db.flush()
    invalidate_feature_cache(FEATURE_MOCK_EXAM)


def _build_tickets_from_form(db: DBSession, assignment, form, ticket_count: int) -> None:
    """Парсит ticket_{i}_* поля формы и создаёт ExamTicket + ExamTicketAssignee.
    Используется и при создании, и при редактировании (после удаления старых тикетов).
    """
    for i in range(1, ticket_count + 1):
        t_title = str(form.get(f"ticket_{i}_title", "")).strip()
        t_desc = str(form.get(f"ticket_{i}_description", "")).strip() or None
        t_img_url = str(form.get(f"ticket_{i}_image_url", "")).strip() or None
        t_img_path = str(form.get(f"ticket_{i}_image_path", "")).strip() or None
        t_activate_mode = str(form.get(f"ticket_{i}_activate_mode", "scheduled")).strip()
        t_start_raw = str(form.get(f"ticket_{i}_start_date", "")).strip()
        t_end_raw = str(form.get(f"ticket_{i}_end_date", "")).strip()
        t_all = form.get(f"ticket_{i}_assign_all") == "on"
        t_students_raw = str(form.get(f"ticket_{i}_student_ids", "")).strip()

        if not t_title:
            raise HTTPException(status_code=422, detail=f"Название билета {i} обязательно")

        try:
            t_end = date.fromisoformat(t_end_raw)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Неверная дата окончания в билете {i}")

        if t_activate_mode == "now":
            t_start = today_msk()
        else:
            try:
                t_start = date.fromisoformat(t_start_raw)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Неверная дата начала в билете {i}")

        if t_end < t_start:
            raise HTTPException(status_code=422, detail=f"Дата окончания раньше начала в билете {i}")
        if t_end < today_msk():
            raise HTTPException(status_code=422, detail=f"Билет {i}: дата окончания уже в прошлом")

        ticket = ExamTicket(
            assignment_id=assignment.id,
            ticket_number=i,
            title=t_title,
            description=t_desc,
            image_s3_url=t_img_url,
            image_s3_path=t_img_path,
            start_date=t_start,
            end_date=t_end,
            assign_to_all=t_all,
        )
        db.add(ticket)
        db.flush()

        if not t_all:
            student_ids = list({int(x) for x in t_students_raw.split(",") if x.strip().isdigit()})
            if student_ids:
                stmt = pg_insert(ExamTicketAssignee.__table__).values(
                    [{"ticket_id": ticket.id, "user_id": uid} for uid in student_ids]
                ).on_conflict_do_nothing(index_elements=["ticket_id", "user_id"])
                db.execute(stmt)


@router.post("/exam-assignments/{assignment_id}/edit")
async def exam_assignment_edit_submit(
    assignment_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    assignment = db.query(ExamAssignment).filter(ExamAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    form = await request.form()
    title = str(form.get("title", "")).strip()
    subject = str(form.get("subject", "")).strip()
    ticket_count = int(form.get("ticket_count", 1) or 1)
    ticket_count = max(1, min(10, ticket_count))

    if not title:
        raise HTTPException(status_code=422, detail="Название задания обязательно")
    if subject not in MOCK_SUBJECTS:
        raise HTTPException(status_code=422, detail="Неверный предмет")

    # Обновляем метаданные задания
    assignment.title = title
    assignment.subject = subject

    # Full-replace тикетов: удаляем старые assignees + tickets, затем создаём из формы.
    # Notification history (notified_at) теряется — это допустимо при редактировании.
    old_ticket_ids = [tid for (tid,) in db.query(ExamTicket.id).filter(ExamTicket.assignment_id == assignment_id).all()]
    if old_ticket_ids:
        db.query(ExamTicketAssignee).filter(ExamTicketAssignee.ticket_id.in_(old_ticket_ids)).delete(synchronize_session=False)
        db.query(ExamTicket).filter(ExamTicket.id.in_(old_ticket_ids)).delete(synchronize_session=False)
        db.flush()

    _build_tickets_from_form(db, assignment, form, ticket_count)
    _ensure_mock_exam_period_open(db, user["user_id"])
    db.commit()
    return RedirectResponse(
        f"/cabinet/exam-assignments/{assignment_id}", status_code=303
    )


# ── Просмотр задания ─────────────────────────────────────────────────────────

@router.get("/exam-assignments/{assignment_id}", response_class=HTMLResponse)
def exam_assignment_detail(
    assignment_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    assignment = db.query(ExamAssignment).filter(ExamAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    tickets = (
        db.query(ExamTicket)
        .filter(ExamTicket.assignment_id == assignment_id)
        .order_by(ExamTicket.ticket_number)
        .all()
    )

    # Load all assignees in one query, then group in memory (avoids N+1)
    assignees_by_ticket: dict[int, list[dict]] = {t.id: [] for t in tickets}
    non_all_ticket_ids = [t.id for t in tickets if not t.assign_to_all]
    if non_all_ticket_ids:
        rows = (
            db.query(User.id, User.first_name, User.last_name, User.name,
                     ExamTicketAssignee.ticket_id, ExamTicketAssignee.notified_at)
            .join(ExamTicketAssignee, ExamTicketAssignee.user_id == User.id)
            .filter(ExamTicketAssignee.ticket_id.in_(non_all_ticket_ids))
            .all()
        )
        for r in rows:
            assignees_by_ticket.setdefault(r.ticket_id, []).append({
                "id": r.id,
                "name": f"{r.last_name or ''} {r.first_name or r.name}".strip(),
                "notified": r.notified_at is not None,
            })

    creator = db.query(User).filter(User.id == assignment.created_by_id).first()

    return templates.TemplateResponse("superadmin_exam_assignment_detail.html", {
        "request": request,
        "user": user,
        "assignment": assignment,
        "tickets": tickets,
        "assignees_by_ticket": assignees_by_ticket,
        "creator": creator,
    })


# ── Архивирование задания ────────────────────────────────────────────────────

@router.post("/exam-assignments/{assignment_id}/archive")
def exam_assignment_archive(
    assignment_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    assignment = db.query(ExamAssignment).filter(ExamAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    assignment.status = "archived"
    db.commit()
    return RedirectResponse("/cabinet/exam-assignments", status_code=303)


# ── Feature periods ───────────────────────────────────────────────────────────

ALL_FEATURES = [FEATURE_PORTFOLIO_UPLOAD, FEATURE_MOCK_EXAM, FEATURE_RETAKE]


@router.get("/periods", response_class=HTMLResponse)
def periods_list(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    periods = (
        db.query(FeaturePeriod)
        .order_by(FeaturePeriod.feature, FeaturePeriod.start_date.desc())
        .all()
    )
    today = today_msk()
    return templates.TemplateResponse("periods_management.html", {
        "request": request,
        "user": user,
        "periods": periods,
        "features": ALL_FEATURES,
        "feature_labels": FEATURE_LABELS,
        "today": today,
    })


@router.post("/periods/create")
def period_create(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    feature: Annotated[str, Form()],
    start_date: Annotated[str, Form()],
    end_date: Annotated[str, Form()],
    title: Annotated[str, Form()] = "",
):
    if feature not in ALL_FEATURES:
        raise HTTPException(status_code=400, detail="Неверный тип периода")
    try:
        from datetime import date as _date
        sd = _date.fromisoformat(start_date)
        ed = _date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты")
    if sd > ed:
        raise HTTPException(status_code=400, detail="Дата начала должна быть раньше даты окончания")

    period = FeaturePeriod(
        feature=feature,
        title=title.strip() or None,
        start_date=sd,
        end_date=ed,
        created_by_id=user["user_id"],
    )
    db.add(period)
    db.commit()
    invalidate_feature_cache(feature)
    return RedirectResponse("/cabinet/periods", status_code=303)


@router.post("/periods/{period_id}/deactivate")
def period_deactivate(
    period_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    period = db.query(FeaturePeriod).filter(FeaturePeriod.id == period_id).first()
    if not period:
        raise HTTPException(status_code=404, detail="Период не найден")
    period.is_active = False
    db.commit()
    invalidate_feature_cache(period.feature)
    return RedirectResponse("/cabinet/periods", status_code=303)


@router.post("/periods/{period_id}/activate")
def period_activate(
    period_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    period = db.query(FeaturePeriod).filter(FeaturePeriod.id == period_id).first()
    if not period:
        raise HTTPException(status_code=404, detail="Период не найден")
    period.is_active = True
    db.commit()
    invalidate_feature_cache(period.feature)
    return RedirectResponse("/cabinet/periods", status_code=303)


@router.post("/periods/quick-toggle")
def period_quick_toggle(
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    feature: Annotated[str, Form()],
    redirect_to: Annotated[str, Form()] = "/cabinet",
):
    """Быстрое включение/выключение периода с дашборда.

    Если есть активный период на сегодня → деактивирует его.
    Если нет → создаёт период «сейчас + 30 дней».
    """
    if feature not in ALL_FEATURES:
        raise HTTPException(status_code=400, detail="Неверный тип периода")

    from datetime import timedelta
    today = today_msk()
    active_period = (
        db.query(FeaturePeriod)
        .filter(
            FeaturePeriod.feature == feature,
            FeaturePeriod.is_active.is_(True),
            FeaturePeriod.start_date <= today,
            FeaturePeriod.end_date >= today,
        )
        .first()
    )

    if active_period:
        active_period.is_active = False
    else:
        db.add(FeaturePeriod(
            feature=feature,
            title="Быстрый доступ",
            start_date=today,
            end_date=today + timedelta(days=30),
            created_by_id=user["user_id"],
        ))

    db.commit()
    invalidate_feature_cache(feature)

    safe_redirect = redirect_to if redirect_to.startswith("/") and not redirect_to.startswith("//") else "/cabinet"
    return RedirectResponse(safe_redirect, status_code=303)


# ═══════════════════════════════════════════════════════════════════════════════
# Статистика периодов сдачи
# ═══════════════════════════════════════════════════════════════════════════════

from app.services.period_stats import get_submission_stats, get_all_periods as _get_all_periods


@router.get("/superadmin/stats", response_class=HTMLResponse)
def superadmin_stats(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    period_id: int | None = None,
    feature: str | None = None,
):
    periods = _get_all_periods(db)
    stats = get_submission_stats(db, feature=feature, period_id=period_id)
    return templates.TemplateResponse("superadmin_stats.html", {
        "request": request,
        "user": user,
        "periods": periods,
        "stats": stats,
        "selected_period_id": period_id,
        "selected_feature": feature,
        "feature_labels": FEATURE_LABELS,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Управление пользователями
# ═══════════════════════════════════════════════════════════════════════════════

from app.services.user_management import soft_delete_user, toggle_user_active


_SU_PAGE_SIZE = 50


@router.get("/superadmin/users", response_class=HTMLResponse)
def superadmin_users(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    q: str = "",
    role_rank: str = Query(default=""),
    tariff: str = "",
    show_deleted: str = "",
    show_blocked: str = "",
    page: int = 1,
):
    role_rank_int: int | None = int(role_rank) if role_rank.strip() else None
    show_deleted: bool = show_deleted.strip() in ("1", "true", "on", "yes")
    show_blocked: bool = show_blocked.strip() in ("1", "true", "on", "yes")

    page = max(1, page)
    query = db.query(User).outerjoin(Role, User.role_id == Role.id)
    if show_blocked:
        query = query.filter(User.is_active == False, User.deleted_at.is_(None))  # noqa: E712
    elif not show_deleted:
        query = query.filter(User.deleted_at.is_(None))
    if role_rank_int is not None:
        query = query.filter(Role.rank == role_rank_int)
    if tariff.strip():
        query = query.filter(User.tariff == tariff.strip())
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            User.name.ilike(like) |
            User.first_name.ilike(like) |
            User.last_name.ilike(like)
        )
    total = query.count()
    total_pages = max(1, (total + _SU_PAGE_SIZE - 1) // _SU_PAGE_SIZE)
    page = min(page, total_pages)
    users = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * _SU_PAGE_SIZE)
        .limit(_SU_PAGE_SIZE)
        .all()
    )
    roles = db.query(Role).order_by(Role.rank).all()
    return templates.TemplateResponse("superadmin_users.html", {
        "request": request,
        "user": user,
        "users": users,
        "roles": roles,
        "tariffs": TARIFFS,
        "q": q,
        "role_rank": role_rank,
        "tariff": tariff,
        "show_deleted": show_deleted,
        "show_blocked": show_blocked,
        "current_user_id": user["user_id"],
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.post("/superadmin/users/{target_id}/delete")
def superadmin_delete_user(
    target_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    ok = soft_delete_user(db, target_user_id=target_id, performed_by_id=user["user_id"])
    if not ok:
        raise HTTPException(status_code=400, detail="Невозможно удалить пользователя")
    return RedirectResponse("/cabinet/superadmin/users", status_code=303)


@router.post("/superadmin/users/{target_id}/toggle-active")
def superadmin_toggle_active(
    target_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    result = toggle_user_active(db, target_user_id=target_id, performed_by_id=user["user_id"])
    if result is None:
        raise HTTPException(status_code=400, detail="Невозможно изменить статус пользователя")
    return RedirectResponse("/cabinet/superadmin/users", status_code=303)
