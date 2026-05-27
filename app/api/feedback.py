"""Роуты обратной связи (редизайн 2026-05-23 — диалог).

Студент:
  GET  /cabinet/feedback/                       — список открытых циклов студента
  GET  /cabinet/feedback/{cycle_id}             — детали цикла + диалог

Студент / куратор / админ / суперадмин:
  POST /cabinet/feedback/{work_id}/message      — отправить сообщение
                                                   (студент — только если есть feedback с
                                                    хотя бы одним сообщением staff)

Staff (куратор / админ / суперадмин):
  GET  /cabinet/staff/cycles                    — все циклы для просмотра
  GET  /cabinet/staff/cycle/probnik/{user_id}   — календарь Пробника ученика (legacy)
  GET  /cabinet/staff/cycle/otrabotka/{user_id} — календарь Отработки ученика (legacy)
  GET  /cabinet/curator/feedback/{cycle_id}     — диалог цикла (куратор)
  GET  /cabinet/admin/feedback/{cycle_id}       — диалог цикла (админ)
  GET  /cabinet/superadmin/feedback/{cycle_id}  — диалог цикла (суперадмин)

JSON:
  GET  /cabinet/students/{student_id}/cycles    — список циклов ученика для staff
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import (
    get_current_user,
    require_curator,
    require_superadmin,
    require_csrf,
)
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services import feedback as fb_service
from app.services.exam_cycle import has_open_cycles
from app.tmpl import templates

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_SIZE = 10 * 1024 * 1024
MAX_TEXT_LEN = 4000


def _serialize_cycle(cycle: ExamCycle, finals: list[Work], unread_count: int = 0) -> dict:
    return {
        "id": cycle.id,
        "subject": cycle.subject,
        "started_at": cycle.started_at.isoformat(),
        "closed_at": cycle.closed_at.isoformat() if cycle.closed_at else None,
        "ticket_id": cycle.ticket_id,
        "attempts": len(finals),
        "unread_count": unread_count,
    }


def _dialog_payload(db: DBSession, cycle: ExamCycle) -> dict:
    """Контекст для диалога: финальные попытки цикла + промежуточные + сообщения."""
    finals = (
        db.query(Work)
        .filter(Work.cycle_id == cycle.id, Work.is_final == True)  # noqa: E712
        .order_by(Work.work_type, Work.attempt_number, Work.id)
        .all()
    )
    if not finals:
        return {"attempts": [], "feedbacks": {}}

    final_ids = [w.id for w in finals]

    intermediates_by_parent: dict[int, list[Work]] = {}
    for w in (
        db.query(Work)
        .filter(Work.parent_work_id.in_(final_ids), Work.is_final == False)  # noqa: E712
        .order_by(Work.created_at, Work.id)
        .all()
    ):
        intermediates_by_parent.setdefault(w.parent_work_id, []).append(w)

    feedbacks_by_work = {
        f.work_id: f
        for f in db.query(Feedback).filter(Feedback.work_id.in_(final_ids)).all()
    }
    fb_ids = [f.id for f in feedbacks_by_work.values()]
    messages_by_fb: dict[int, list[FeedbackMessage]] = {}
    if fb_ids:
        for m in (
            db.query(FeedbackMessage)
            .filter(FeedbackMessage.feedback_id.in_(fb_ids))
            .order_by(FeedbackMessage.created_at, FeedbackMessage.id)
            .all()
        ):
            messages_by_fb.setdefault(m.feedback_id, []).append(m)

    attempts: list[dict] = []
    for w in finals:
        fb = feedbacks_by_work.get(w.id)
        messages = fb_service.serialize_messages(messages_by_fb.get(fb.id, [])) if fb else []
        attempts.append({
            "work_id": w.id,
            "work_type": w.work_type,
            "attempt_number": w.attempt_number,
            "subject": w.subject,
            "s3_url": w.s3_url,
            "filename": w.filename,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "score": float(w.score) if w.score is not None else None,
            "intermediates": [
                {"id": iw.id, "s3_url": iw.s3_url, "filename": iw.filename}
                for iw in intermediates_by_parent.get(w.id, [])
            ],
            "feedback_id": fb.id if fb else None,
            "has_staff_message": any(
                m["sender_role"] != fb_service.ROLE_STUDENT for m in messages
            ),
            "messages": messages,
        })
    return {"attempts": attempts}


# ── Студент ──────────────────────────────────────────────────────────────────

@router.get("/cabinet/feedback/", response_class=HTMLResponse)
def student_feedback_list(
    user: Annotated[dict, Depends(get_current_user)],
):
    if user["role_rank"] != 1:
        return RedirectResponse("/cabinet/staff/cycles", status_code=302)
    return RedirectResponse("/cabinet/cycle?tab=feedback", status_code=302)


@router.get("/cabinet/feedback/{cycle_id}", response_class=HTMLResponse)
def student_feedback_detail(
    request: Request,
    cycle_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if user["role_rank"] != 1:
        # Staff: переадресовать на свой роут
        if user["role_rank"] >= 5:
            return RedirectResponse(f"/cabinet/superadmin/feedback/{cycle_id}", status_code=302)
        if user["role_rank"] >= 4:
            return RedirectResponse(f"/cabinet/admin/feedback/{cycle_id}", status_code=302)
        return RedirectResponse(f"/cabinet/curator/feedback/{cycle_id}", status_code=302)

    from app.models.notification import Notification

    cycle = (
        db.query(ExamCycle)
        .filter(ExamCycle.id == cycle_id, ExamCycle.user_id == user["user_id"])
        .first()
    )
    if not cycle:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    if cycle.closed_at is not None:
        raise HTTPException(status_code=404, detail="Цикл уже закрыт")

    payload = _dialog_payload(db, cycle)

    # Mark unread notifications for this cycle as read
    work_ids = [a["work_id"] for a in payload["attempts"]]
    if work_ids:
        db.query(Notification).filter(
            Notification.user_id == user["user_id"],
            Notification.work_id.in_(work_ids),
            Notification.is_read == False,  # noqa: E712
        ).update({"is_read": True}, synchronize_session=False)
        db.commit()

    return templates.TemplateResponse("cabinet_feedback_detail.html", {
        "request": request, "user": user,
        "cycle": _serialize_cycle(cycle, [a for a in payload["attempts"]]),
        "attempts": payload["attempts"],
        "viewer_role": "student",
        "student": {"id": user["user_id"], "name": user.get("name", "")},
        "back_url": "/cabinet/feedback/",
        "back_label": "К списку",
    })


# ── Универсальный POST: отправить сообщение в диалог ──────────────────────────

@router.post("/cabinet/feedback/{work_id}/message")
async def post_dialog_message(
    work_id: int,
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    text: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
):
    work = db.query(Work).filter(Work.id == work_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if not work.is_final or work.work_type != WORK_TYPE_MOCK_EXAM:
        raise HTTPException(status_code=422, detail="Диалог доступен только на финальной попытке Пробника")

    if work.cycle_id is None:
        raise HTTPException(status_code=422, detail="Работа не привязана к циклу")
    cycle = db.query(ExamCycle).filter(ExamCycle.id == work.cycle_id).first()
    if cycle is None or cycle.closed_at is not None:
        raise HTTPException(status_code=403, detail="Цикл закрыт — отправка сообщений недоступна")

    student = db.query(User).filter(User.id == work.user_id).first()
    if not student or student.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Студент не найден")

    role_rank = user["role_rank"]
    sender_role = fb_service.role_from_rank(role_rank)

    # ── Авторизация
    if sender_role == fb_service.ROLE_STUDENT:
        if user["user_id"] != student.id:
            raise HTTPException(status_code=403, detail="Это не ваш цикл")
        fb = db.query(Feedback).filter(Feedback.work_id == work_id).first()
        if fb is None:
            raise HTTPException(status_code=403, detail="Жди обратной связи куратора, потом сможешь ответить")
        has_staff_msg = (
            db.query(FeedbackMessage)
            .filter(
                FeedbackMessage.feedback_id == fb.id,
                FeedbackMessage.sender_role != fb_service.ROLE_STUDENT,
            )
            .first()
            is not None
        )
        if not has_staff_msg:
            raise HTTPException(status_code=403, detail="Жди обратной связи куратора, потом сможешь ответить")
        recipient_id = fb.curator_id
    else:
        # Куратор — только своим студентам. Админ/SA — кому угодно.
        if role_rank == 2 and student.curator_id != user["user_id"]:
            raise HTTPException(status_code=403, detail="Это не ваш студент")
        if "feedback.write" not in user.get("permissions", set()):
            raise HTTPException(status_code=403, detail="Нет прав на запись feedback")
        fb, _created = fb_service.get_or_create_feedback(
            db, work_id=work_id, initiator_id=user["user_id"]
        )
        recipient_id = student.id

    # ── Сбор payload
    text_clean = (text or "").strip()
    if len(text_clean) > MAX_TEXT_LEN:
        text_clean = text_clean[:MAX_TEXT_LEN]

    photo_payload: tuple[str, bytes] | None = None
    if photo and photo.filename:
        data = await photo.read()
        if len(data) > MAX_SIZE:
            raise HTTPException(status_code=413, detail="Фото больше 10 МБ")
        if data:
            photo_payload = (photo.filename, data)

    if not text_clean and photo_payload is None:
        raise HTTPException(status_code=400, detail="Введи текст или прикрепи фото")

    try:
        msg = await fb_service.send_message(
            db,
            feedback=fb,
            sender_id=user["user_id"],
            sender_role=sender_role,
            text=text_clean or None,
            photo=photo_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    fb_service.notify_counterpart(
        db, work=work, recipient_id=recipient_id, sender_role=sender_role
    )

    db.commit()

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({
            "ok": True,
            "message": {
                "id": msg.id,
                "sender_role": msg.sender_role,
                "text": msg.text,
                "photo_s3_url": msg.photo_s3_url,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            },
        })
    # HTML form fallback
    if sender_role == fb_service.ROLE_STUDENT:
        return RedirectResponse(f"/cabinet/feedback/{cycle.id}#m-{msg.id}", status_code=302)
    if role_rank >= 5:
        return RedirectResponse(f"/cabinet/superadmin/feedback/{cycle.id}#m-{msg.id}", status_code=302)
    if role_rank >= 4:
        return RedirectResponse(f"/cabinet/admin/feedback/{cycle.id}#m-{msg.id}", status_code=302)
    return RedirectResponse(f"/cabinet/curator/feedback/{cycle.id}#m-{msg.id}", status_code=302)


# ── Staff: список циклов (куратор/админ/SA) ──────────────────────────────────

def _staff_cycles_data(db: DBSession, user: dict) -> list[dict]:
    q = (
        db.query(ExamCycle, User)
        .join(User, ExamCycle.user_id == User.id)
        .filter(User.deleted_at.is_(None))
    )
    if user["role_rank"] == 2:
        q = q.filter(User.curator_id == user["user_id"])
    q = q.order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
    rows = q.limit(500).all()
    if not rows:
        return []
    cycle_ids = [c.id for c, _ in rows]
    finals_by_cycle: dict[int, list[Work]] = {}
    for w in (
        db.query(Work)
        .filter(Work.cycle_id.in_(cycle_ids), Work.is_final == True)  # noqa: E712
        .all()
    ):
        finals_by_cycle.setdefault(w.cycle_id, []).append(w)
    fb_work_ids = {
        row[0] for row in db.query(Feedback.work_id).join(Work, Feedback.work_id == Work.id)
        .filter(Work.cycle_id.in_(cycle_ids)).all()
    }
    items = []
    for cycle, student in rows:
        finals = finals_by_cycle.get(cycle.id, [])
        items.append({
            "id": cycle.id,
            "subject": cycle.subject,
            "started_at": cycle.started_at.isoformat(),
            "closed_at": cycle.closed_at.isoformat() if cycle.closed_at else None,
            "attempts": len(finals),
            "feedbacks_count": sum(1 for w in finals if w.id in fb_work_ids),
            "student_id": student.id,
            "student_name": student.name,
        })
    return items


@router.get("/cabinet/staff/cycles", response_class=HTMLResponse)
def staff_cycles_list(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    items = _staff_cycles_data(db, user)
    by_student: dict[int, dict] = {}
    for it in items:
        sid = it["student_id"]
        if sid not in by_student:
            by_student[sid] = {
                "student_id": sid,
                "student_name": it["student_name"],
                "cycles": [],
            }
        by_student[sid]["cycles"].append(it)
    students_sorted = sorted(by_student.values(), key=lambda s: s["student_name"] or "")
    if user["role_rank"] >= 5:
        detail_prefix = "/cabinet/superadmin/feedback/"
    elif user["role_rank"] >= 4:
        detail_prefix = "/cabinet/admin/feedback/"
    else:
        detail_prefix = "/cabinet/curator/feedback/"
    return templates.TemplateResponse("cabinet_staff_cycles.html", {
        "request": request, "user": user,
        "students": students_sorted,
        "total_cycles": len(items),
        "detail_prefix": detail_prefix,
    })


# ── JSON: циклы конкретного ученика (для вкладки в карточке staff) ───────────

@router.get("/cabinet/students/{student_id}/cycles")
def student_cycles_json(
    student_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = db.query(User).filter(User.id == student_id, User.deleted_at.is_(None)).first()
    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")
    if user["role_rank"] == 2 and student.curator_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Не ваш студент")
    cycles = (
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == student_id)
        .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
        .all()
    )
    cycle_ids = [c.id for c in cycles]
    finals_by_cycle: dict[int, list[Work]] = {}
    if cycle_ids:
        for w in (
            db.query(Work)
            .filter(Work.cycle_id.in_(cycle_ids), Work.is_final == True)  # noqa: E712
            .all()
        ):
            finals_by_cycle.setdefault(w.cycle_id, []).append(w)
    fb_work_ids: set[int] = set()
    if cycle_ids:
        fb_work_ids = {
            row[0] for row in db.query(Feedback.work_id).join(Work, Feedback.work_id == Work.id)
            .filter(Work.cycle_id.in_(cycle_ids)).all()
        }
    if user["role_rank"] >= 5:
        detail_prefix = "/cabinet/superadmin/feedback/"
    elif user["role_rank"] >= 4:
        detail_prefix = "/cabinet/admin/feedback/"
    else:
        detail_prefix = "/cabinet/curator/feedback/"
    items = []
    for c in cycles:
        finals = finals_by_cycle.get(c.id, [])
        items.append({
            "id": c.id,
            "subject": c.subject,
            "started_at": c.started_at.isoformat(),
            "closed_at": c.closed_at.isoformat() if c.closed_at else None,
            "attempts": len(finals),
            "feedbacks_count": sum(1 for w in finals if w.id in fb_work_ids),
            "url": f"{detail_prefix}{c.id}",
        })
    return JSONResponse({
        "student_id": student_id,
        "student_name": student.name,
        "cycles": items,
    })


# ── Staff: диалог цикла (read+write) ─────────────────────────────────────────

def _staff_dialog_detail(db: DBSession, request: Request, user: dict, cycle_id: int):
    cycle = db.query(ExamCycle).filter(ExamCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    student = db.query(User).filter(User.id == cycle.user_id).first()
    if not student or student.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Студент не найден")
    if user["role_rank"] == 2 and student.curator_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Это не ваш студент")

    payload = _dialog_payload(db, cycle)
    if user["role_rank"] >= 5:
        back_url = "/cabinet/staff/cycles"
        viewer_role = "superadmin"
    elif user["role_rank"] >= 4:
        back_url = "/cabinet/staff/cycles"
        viewer_role = "admin"
    else:
        back_url = "/cabinet/staff/cycles"
        viewer_role = "curator"
    return templates.TemplateResponse("cabinet_feedback_detail.html", {
        "request": request, "user": user,
        "cycle": _serialize_cycle(cycle, [a for a in payload["attempts"]]),
        "attempts": payload["attempts"],
        "viewer_role": viewer_role,
        "student": {"id": student.id, "name": student.name},
        "back_url": back_url,
        "back_label": "К списку циклов",
    })


@router.get("/cabinet/curator/feedback/{cycle_id}", response_class=HTMLResponse)
def curator_feedback_detail(
    request: Request,
    cycle_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    return _staff_dialog_detail(db, request, user, cycle_id)


@router.get("/cabinet/admin/feedback/{cycle_id}", response_class=HTMLResponse)
def admin_feedback_detail(
    request: Request,
    cycle_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if user["role_rank"] < 4:
        raise HTTPException(status_code=403, detail="Только для админа")
    return _staff_dialog_detail(db, request, user, cycle_id)


@router.get("/cabinet/superadmin/feedback/{cycle_id}", response_class=HTMLResponse)
def superadmin_feedback_detail(
    request: Request,
    cycle_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
):
    return _staff_dialog_detail(db, request, user, cycle_id)


# ── Staff: календарь работ конкретного ученика (legacy, для карточки) ────────

def _ensure_staff_can_view_student(db: DBSession, user: dict, student_id: int) -> User:
    student = db.query(User).filter(User.id == student_id, User.deleted_at.is_(None)).first()
    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")
    if user["role_rank"] == 2 and student.curator_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Не ваш студент")
    return student


@router.get("/cabinet/staff/cycle/probnik/{student_id}", response_class=HTMLResponse)
def staff_student_probnik_calendar(
    request: Request,
    student_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    from app.api.cabinet_student import render_cycle_calendar
    from app.constants import FEATURE_MOCK_EXAM
    from app.models.work import WORK_TYPE_MOCK_EXAM as WT_MOCK

    student = _ensure_staff_can_view_student(db, user, student_id)
    return render_cycle_calendar(
        request, user, db,
        target_user_id=student.id,
        work_type=WT_MOCK,
        page_title="Пробник",
        upload_url="",
        upload_label="",
        feature_key=FEATURE_MOCK_EXAM,
        active_tab="mock",
        staff_view=True,
        student_name=student.name,
        back_url=f"/cabinet/students?student={student.id}&tab=cycles",
        back_label="К карточке ученика",
    )


@router.get("/cabinet/staff/cycle/otrabotka/{student_id}", response_class=HTMLResponse)
def staff_student_otrabotka_calendar(
    request: Request,
    student_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    from app.api.cabinet_student import render_cycle_calendar
    from app.constants import FEATURE_RETAKE
    from app.models.work import WORK_TYPE_RETAKE as WT_RET

    student = _ensure_staff_can_view_student(db, user, student_id)
    return render_cycle_calendar(
        request, user, db,
        target_user_id=student.id,
        work_type=WT_RET,
        page_title="Отработка",
        upload_url="",
        upload_label="",
        feature_key=FEATURE_RETAKE,
        active_tab="retake",
        staff_view=True,
        student_name=student.name,
        back_url=f"/cabinet/students?student={student.id}&tab=cycles",
        back_label="К карточке ученика",
    )
