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
  POST /cabinet/feedback/{cycle_id}/close       — закрыть цикл вручную после ОС
                                                   (куратор/админ/SA, требует выставленного балла)

JSON:
  GET  /cabinet/students/{student_id}/cycles    — список циклов ученика для staff
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import and_ as sa_and, or_ as sa_or
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.db.database import get_db
from app.dependencies import (
    get_current_user,
    require_curator,
    require_superadmin,
    require_csrf,
)
from app.services.notify import notify
from app.models.exam_cycle import ExamCycle
from app.models.exam_assignment import ExamTicket
from app.models.feedback import Feedback, FeedbackMessage
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services import feedback as fb_service
from app.services.exam_cycle import (
    close_cycle,
    delete_open_cycle,
    finish_curator_revision,
    has_open_cycles,
    reopen_cycle,
    request_curator_revision,
)
from app.services.student_access import get_student_for_staff_access
from app.services.utils import has_case_growth
from app.tmpl import templates

from collections import defaultdict

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_TEXT_LEN = 4000


def _serialize_cycle(cycle: ExamCycle, finals: list[Work], unread_count: int = 0) -> dict:
    return {
        "id": cycle.id,
        "subject": cycle.subject,
        "started_at": cycle.started_at.isoformat(),
        "closed_at": cycle.closed_at.isoformat() if cycle.closed_at else None,
        "intermediate_score": (
            float(cycle.intermediate_score) if cycle.intermediate_score is not None else None
        ),
        # Ключ читают шаблоны как признак «на правке»: после завершения правки
        # (revision_done_at) отдаём None, хотя requested_at в БД сохраняется.
        "revision_requested_at": (
            cycle.revision_requested_at.isoformat() if cycle.is_on_revision else None
        ),
        "ticket_id": cycle.ticket_id,
        "attempts": len(finals),
        "unread_count": unread_count,
    }


def _dialog_payload(db: DBSession, cycle: ExamCycle) -> dict:
    """Контекст для диалога: финальные попытки цикла + промежуточные + сообщения."""
    ticket = db.get(ExamTicket, cycle.ticket_id) if cycle.ticket_id else None
    ticket_payload = (
        {
            "title": ticket.title,
            "image_url": ticket.image_s3_url or "",
        }
        if ticket else None
    )
    finals = (
        db.query(Work)
        .filter(Work.cycle_id == cycle.id, Work.is_final == True)  # noqa: E712
        .order_by(Work.work_type, Work.attempt_number, Work.id)
        .all()
    )
    if not finals:
        return {
            "attempts": [], "feedbacks": {}, "ticket": ticket_payload,
            "student_upload_open": False,
        }

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

    # Имена авторов сообщений: показываем «Имя · Роль» вместо одной роли.
    sender_ids = {
        m.sender_id for msgs in messages_by_fb.values() for m in msgs
    }
    names: dict[int, str] = {}
    if sender_ids:
        names = {
            uid: name
            for uid, name in db.query(User.id, User.name)
            .filter(User.id.in_(sender_ids))
            .all()
        }

    attempts: list[dict] = []
    for w in finals:
        fb = feedbacks_by_work.get(w.id)
        messages = fb_service.serialize_messages(messages_by_fb.get(fb.id, []), names) if fb else []
        attempts.append({
            "work_id": w.id,
            "work_type": w.work_type,
            "attempt_number": w.attempt_number,
            "subject": w.subject,
            "s3_url": w.s3_url,
            "filename": w.filename,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "score": float(w.score) if w.score is not None else None,
            "comment": w.comment or "",
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

    # Единый диалог на цикл: все сообщения всех финалок в одной хронологической ленте.
    all_msgs: list[FeedbackMessage] = []
    for msgs in messages_by_fb.values():
        all_msgs.extend(msgs)
    all_msgs.sort(key=lambda m: (m.created_at or datetime.min.replace(tzinfo=timezone.utc), m.id))
    thread = fb_service.serialize_messages(all_msgs, names)
    has_staff_message = any(
        m["sender_role"] != fb_service.ROLE_STUDENT for m in thread
    )
    # Форма сообщений целится в финалку Пробника: POST /message принимает только
    # mock_exam-финалки (финалка Отработки даст 422). В открытом цикле она одна,
    # поэтому весь диалог цикла привязан к ней — гейт показа и POST совпадают.
    target = next(
        (w for w in finals if w.work_type == WORK_TYPE_MOCK_EXAM), finals[-1]
    )
    return {
        "attempts": attempts,
        "thread": thread,
        "target_work_id": target.id,
        "has_staff_message": has_staff_message,
        "ticket": ticket_payload,
        "student_upload_open": any(
            w.work_type == WORK_TYPE_MOCK_EXAM and w.needs_revision
            for w in finals
        ),
    }


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

    payload = _dialog_payload(db, cycle)

    # Mark unread notifications for this cycle as read
    work_ids = [a["work_id"] for a in payload["attempts"]]
    if work_ids:
        db.query(Notification).filter(
            Notification.user_id == user["user_id"],
            Notification.work_id.in_(work_ids),
            Notification.is_read == False,  # noqa: E712
        ).update(
            {"is_read": True, "read_at": datetime.now(timezone.utc)},
            synchronize_session=False,
        )
        db.commit()

    return templates.TemplateResponse("cabinet_feedback_detail.html", {
        "request": request, "user": user,
        "cycle": _serialize_cycle(cycle, [a for a in payload["attempts"]]),
        "attempts": payload["attempts"],
        "thread": payload.get("thread", []),
        "ticket": payload.get("ticket"),
        "student_upload_open": payload.get("student_upload_open", False),
        "target_work_id": payload.get("target_work_id"),
        "has_staff_message": payload.get("has_staff_message", False),
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
    background_tasks: BackgroundTasks,
    text: str = Form(default=""),
    impression: str = Form(default=""),
    good: str = Form(default=""),
    strengthen: str = Form(default=""),
    recommendations: str = Form(default=""),
    intermediate_score: str = Form(default=""),
    photo: UploadFile | None = File(default=None),
    video: UploadFile | None = File(default=None),
    audio: UploadFile | None = File(default=None),
):
    work = db.query(Work).filter(Work.id == work_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if not work.is_final or work.work_type != WORK_TYPE_MOCK_EXAM:
        raise HTTPException(status_code=422, detail="Диалог доступен только на финальной попытке Пробника")

    if work.cycle_id is None:
        raise HTTPException(status_code=422, detail="Работа не привязана к циклу")
    cycle = db.query(ExamCycle).filter(ExamCycle.id == work.cycle_id).first()
    if cycle is None:
        raise HTTPException(status_code=404, detail="Цикл не найден")

    student = get_student_for_staff_access(
        db,
        user,
        work.user_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Это не ваш студент",
    )

    role_rank = user["role_rank"]
    sender_role = fb_service.role_from_rank(role_rank)

    # Закрытый цикл запрещает запись только ученику. Staff (куратор/админ/SA)
    # может дать обратную связь и после закрытия — балл, ОС и закрытие — три
    # разных шага (см. close_cycle: закрытие — ручное, требует выставленного балла).
    if cycle.closed_at is not None and sender_role == fb_service.ROLE_STUDENT:
        raise HTTPException(status_code=403, detail="Цикл закрыт — отправка сообщений недоступна")

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
        # feedback.write: куратор/админ/суперадмин, но не модератор (rank 3
        # роль по факту без реальных прав — см. app/services/rbac.py ROLE_PERMISSIONS)
        if role_rank < 2 or role_rank == 3:
            raise HTTPException(status_code=403, detail="Нет прав на запись feedback")
        fb, _created = fb_service.get_or_create_feedback(
            db, work_id=work_id, initiator_id=user["user_id"]
        )
        recipient_id = student.id

    # ── Сбор payload
    text_clean = (text or "").strip()
    intermediate_score_value: int | None = None
    if sender_role != fb_service.ROLE_STUDENT and (intermediate_score or "").strip():
        try:
            intermediate_score_value = int(round(float(intermediate_score)))
        except (ValueError, OverflowError):
            raise HTTPException(
                status_code=422,
                detail="Промежуточный балл должен быть числом от 0 до 100",
            )
        if not 0 <= intermediate_score_value <= 100:
            raise HTTPException(
                status_code=422,
                detail="Промежуточный балл должен быть от 0 до 100",
            )

    # Первая обратная связь staff: структурная форма из 4 пунктов
    # склеивается в одно сообщение с заголовками. Только если свободный
    # текст не введён и отправитель — не студент.
    if not text_clean and sender_role != fb_service.ROLE_STUDENT:
        structured_parts = []
        for label, value in (
            ("Общее впечатление", impression),
            ("Что хорошо", good),
            ("Что улучшить", strengthen),
            ("Рекомендации", recommendations),
        ):
            v = (value or "").strip()
            if v:
                structured_parts.append(f"{label}:\n{v}")
        if structured_parts:
            text_clean = "\n\n".join(structured_parts)

    if len(text_clean) > MAX_TEXT_LEN:
        text_clean = text_clean[:MAX_TEXT_LEN]

    photo_payload: tuple[str, bytes] | None = None
    if photo and photo.filename:
        # Читаем только до лимита + 1 байт: заведомо большой файл не должен
        # целиком попадать в память worker-процесса.
        data = await photo.read(fb_service.MAX_FEEDBACK_PHOTO_INPUT_SIZE + 1)
        if len(data) > fb_service.MAX_FEEDBACK_PHOTO_INPUT_SIZE:
            raise HTTPException(status_code=413, detail="Фото больше 25 МБ")
        if data:
            photo_payload = (photo.filename, data)

    video_payload: tuple[str, bytes, str] | None = None
    if video and video.filename:
        ext = Path(video.filename).suffix.lower()
        content_type = (video.content_type or "").lower()
        if (
            content_type not in fb_service.ALLOWED_FEEDBACK_VIDEO_TYPES
            and ext not in fb_service.ALLOWED_FEEDBACK_VIDEO_EXTENSIONS
        ):
            raise HTTPException(
                status_code=422,
                detail="Видео должно быть в формате mp4, mov, webm, avi, mkv, wmv или 3gp",
            )
        vdata = await video.read(fb_service.MAX_FEEDBACK_VIDEO_SIZE + 1)
        if len(vdata) > fb_service.MAX_FEEDBACK_VIDEO_SIZE:
            raise HTTPException(status_code=413, detail="Видео больше 500 МБ")
        if vdata:
            video_payload = (video.filename, vdata, content_type or "video/mp4")

    audio_payload: tuple[str, bytes, str] | None = None
    if audio and audio.filename:
        ext = Path(audio.filename).suffix.lower()
        audio_content_type = (audio.content_type or "").lower()
        if (
            audio_content_type not in fb_service.ALLOWED_FEEDBACK_AUDIO_TYPES
            and ext not in fb_service.ALLOWED_FEEDBACK_AUDIO_EXTENSIONS
        ):
            raise HTTPException(
                status_code=422,
                detail="Голосовое должно быть в формате mp3, ogg, opus, webm, wav, m4a, aac, amr или 3gp",
            )
        adata = await audio.read(fb_service.MAX_FEEDBACK_AUDIO_SIZE + 1)
        if len(adata) > fb_service.MAX_FEEDBACK_AUDIO_SIZE:
            raise HTTPException(status_code=413, detail="Голосовое больше 25 МБ")
        if adata:
            audio_payload = (audio.filename, adata, audio_content_type or "audio/mpeg")

    if not text_clean and photo_payload is None and video_payload is None and audio_payload is None:
        raise HTTPException(status_code=400, detail="Введи текст, прикрепи фото, видео или голосовое")

    try:
        msg = await fb_service.send_message(
            db,
            feedback=fb,
            sender_id=user["user_id"],
            sender_role=sender_role,
            text=text_clean or None,
            photo=photo_payload,
            video=video_payload,
            audio=audio_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    notification = fb_service.notify_counterpart(
        db, work=work, recipient_id=recipient_id, sender_role=sender_role
    )
    if intermediate_score_value is not None:
        cycle.intermediate_score = intermediate_score_value

    db.commit()
    background_tasks.add_task(notify, notification.id)

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({
            "ok": True,
            "intermediate_score": (
                float(cycle.intermediate_score) if cycle.intermediate_score is not None else None
            ),
            "message": {
                "id": msg.id,
                "sender_role": msg.sender_role,
                "sender_name": user.get("name"),
                "sender_role_label": fb_service.role_label_ru(msg.sender_role),
                "text": msg.text,
                "photo_s3_url": msg.photo_s3_url,
                "video_s3_url": msg.video_s3_url,
                "audio_s3_url": msg.audio_s3_url,
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

def _staff_cycles_data(db: DBSession, user: dict, archived: bool = False) -> list[dict]:
    """Плоский список циклов Пробника для staff.

    archived=False — открытые циклы (closed_at IS NULL), сортировка по дате старта.
    archived=True — архив закрытых циклов (closed_at IS NOT NULL), сортировка по
    дате закрытия (свежезакрытые первыми) — limit 500 берёт самые недавние.

    Каждый элемент несёт идентификацию ученика (имя + @username) и все теги
    как в списке учеников (тариф, период, кол-во занятий, очно/онлайн, КЕЙС,
    когорта) — строка самодостаточна, группировка по ученику не нужна.
    """
    q = (
        db.query(ExamCycle, User)
        .join(User, ExamCycle.user_id == User.id)
        .filter(User.deleted_at.is_(None), User.archived_at.is_(None))
    )
    if archived:
        q = q.filter(ExamCycle.closed_at.isnot(None))
        q = q.order_by(ExamCycle.closed_at.desc(), ExamCycle.id.desc())
    else:
        # Открытые + закрытые, возвращённые куратору на правку ОС: такой цикл
        # должен висеть в рабочем списке куратора, пока правка не завершена
        # (revision_done_at пуст). Curator-фильтр ниже отдаёт ему только его
        # учеников (автор ОС = назначенный куратор).
        q = q.filter(
            sa_or(
                ExamCycle.closed_at.is_(None),
                sa_and(
                    ExamCycle.revision_requested_at.isnot(None),
                    ExamCycle.revision_done_at.is_(None),
                ),
            )
        )
        q = q.order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
    if user["role_rank"] == 2:
        q = q.filter(User.curator_id == user["user_id"])
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
    # Тег КЕЙС: рост score между пробниками одного предмета (как в списке учеников).
    student_ids = {s.id for _, s in rows}
    case_works = (
        db.query(Work.user_id, Work.subject, Work.score, Work.month, Work.year,
                 Work.scored_at, Work.created_at, Work.work_type)
        .filter(
            Work.user_id.in_(student_ids),
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
    has_case_by_user = {uid: has_case_growth(ws) for uid, ws in works_by_uid.items()}
    items = []
    for cycle, student in rows:
        finals = finals_by_cycle.get(cycle.id, [])
        graded = [w for w in finals if w.score is not None]
        final_score = (
            max(graded, key=lambda w: (w.attempt_number or 0, w.id)).score
            if graded else None
        )
        items.append({
            "id": cycle.id,
            "subject": cycle.subject,
            "started_at": cycle.started_at.isoformat(),
            "closed_at": cycle.closed_at.isoformat() if cycle.closed_at else None,
            "revision_requested": cycle.is_on_revision,
            "score": final_score,
            "attempts": len(finals),
            "feedbacks_count": sum(1 for w in finals if w.id in fb_work_ids),
            "student_id": student.id,
            "student_name": student.name,
            "tg_username": (student.tg_username or "").lstrip("@") if user["role_rank"] >= 4 else "",
            "tariff": student.tariff,
            "cohort_tag": student.cohort_tag,
            "course_periods": student.course_periods,
            "lessons_count": student.lessons_count,
            "study_mode": student.study_mode,
            "has_case": has_case_by_user.get(student.id, False),
        })
    return items


@router.get("/cabinet/staff/cycles", response_class=HTMLResponse)
def staff_cycles_list(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    status: str = "open",
):
    # Архив (закрытые циклы) — только админ/SA (role_rank >= 4). Куратору отдаём
    # открытый список даже при ?status=archive (тихо, без 403).
    can_archive = user["role_rank"] >= 4
    archived = status == "archive" and can_archive
    cycles = _staff_cycles_data(db, user, archived=archived)
    if not archived:
        # Открытые: строки одного ученика рядом (стабильная сортировка сохраняет
        # порядок «свежие → старые» из запроса). Архив остаётся в порядке закрытия.
        cycles.sort(key=lambda c: (c["student_name"] or "",))
    if user["role_rank"] >= 5:
        detail_prefix = "/cabinet/superadmin/feedback/"
    elif user["role_rank"] >= 4:
        detail_prefix = "/cabinet/admin/feedback/"
    else:
        detail_prefix = "/cabinet/curator/feedback/"
    return templates.TemplateResponse("cabinet_staff_cycles.html", {
        "request": request, "user": user,
        "cycles": cycles,
        "total_cycles": len(cycles),
        "detail_prefix": detail_prefix,
        "status": "archive" if archived else "open",
        "can_archive": can_archive,
        "nav_active": "cycles",  # подсветка пункта в _curator_nav (куратор)
    })


# ── JSON: циклы конкретного ученика (для вкладки в карточке staff) ───────────

@router.get("/cabinet/students/{student_id}/cycles")
def student_cycles_json(
    student_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = get_student_for_staff_access(
        db,
        user,
        student_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Не ваш студент",
    )
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
    student = get_student_for_staff_access(
        db,
        user,
        cycle.user_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Это не ваш студент",
    )

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
        "thread": payload.get("thread", []),
        "ticket": payload.get("ticket"),
        "student_upload_open": payload.get("student_upload_open", False),
        "target_work_id": payload.get("target_work_id"),
        "has_staff_message": payload.get("has_staff_message", False),
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


@router.post("/cabinet/feedback/{cycle_id}/close")
def close_cycle_route(
    cycle_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Куратор/админ/SA закрывает цикл вручную после того, как дана обратная связь.

    Балл ставится раньше отдельным шагом (score_work) и не закрывает цикл сам.
    close_cycle проверяет, что финалке Пробника уже выставлен балл — иначе 409.
    """
    cycle = db.query(ExamCycle).filter(ExamCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    get_student_for_staff_access(
        db,
        user,
        cycle.user_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Это не ваш студент",
    )
    if cycle.closed_at is not None:
        raise HTTPException(status_code=400, detail="Цикл уже закрыт")
    if not close_cycle(db, cycle):
        raise HTTPException(status_code=409, detail="Сначала нужно выставить балл финальной работе")
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/cabinet/superadmin/feedback/{cycle_id}/delete")
def superadmin_delete_cycle(
    cycle_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Суперадмин удаляет ОТКРЫТЫЙ цикл Пробника (ученик ошибся при отправке).

    Закрытый цикл удалять нельзя — это оценённая история, влияющая на статистику.
    """
    cycle = db.query(ExamCycle).filter(ExamCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    if cycle.closed_at is not None:
        raise HTTPException(status_code=400, detail="Закрытый цикл удалить нельзя")
    s3_paths = delete_open_cycle(db, cycle)
    db.commit()
    # S3 — best-effort после коммита: БД уже source of truth, сбой хранилища не откатывает удаление.
    from app.services.s3 import delete_from_s3
    for path in s3_paths:
        try:
            delete_from_s3(path)
        except Exception:
            logger.warning("S3 cleanup failed during cycle delete: %s", path)
    return JSONResponse({"ok": True})


@router.post("/cabinet/superadmin/feedback/{cycle_id}/reopen")
def superadmin_reopen_cycle(
    cycle_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Суперадмин переоткрывает ЗАКРЫТЫЙ цикл Пробника (закрыли по ошибке).

    Зеркало close: сбрасывает closed_at и возвращает блокировку предмета. Балл и
    наличие работы в Портфолио не трогаются (Портфолио гейтится баллом, не закрытием).
    Запрещено, если у ученика уже есть другой открытый цикл по этому предмету —
    иначе получим два открытых цикла одного предмета.
    """
    cycle = db.query(ExamCycle).filter(ExamCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    get_student_for_staff_access(
        db,
        user,
        cycle.user_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Это не ваш студент",
    )
    if cycle.closed_at is None:
        raise HTTPException(status_code=400, detail="Цикл не закрыт")
    other_open = (
        db.query(ExamCycle.id)
        .filter(
            ExamCycle.user_id == cycle.user_id,
            ExamCycle.subject == cycle.subject,
            ExamCycle.closed_at.is_(None),
            ExamCycle.id != cycle.id,
        )
        .first()
    )
    if other_open is not None:
        raise HTTPException(
            status_code=409,
            detail="У ученика уже есть открытый цикл по этому предмету",
        )
    reopen_cycle(db, cycle)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/cabinet/superadmin/feedback/{cycle_id}/return-to-curator")
def superadmin_return_to_curator(
    cycle_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Суперадмин возвращает цикл (любой — открытый или закрытый) автору ОС на
    правку сообщения.

    Статус цикла не меняем (балл/портфолио/блокировка/closed_at не трогаются).
    Ставим флаг revision_requested_at — он подсвечивает цикл в списке куратора и
    открывает куратору правку своих сообщений (см. edit_feedback_message).
    Снимается, когда куратор нажимает «Завершить правку» (revision-done).
    Требует, чтобы в цикле была обратная связь (есть автор, чьё сообщение править).
    """
    cycle = db.query(ExamCycle).filter(ExamCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    get_student_for_staff_access(
        db,
        user,
        cycle.user_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Это не ваш студент",
    )
    if cycle.is_on_revision:
        raise HTTPException(status_code=400, detail="Цикл уже возвращён куратору на изменение")
    author_id = request_curator_revision(db, cycle)
    if author_id is None:
        raise HTTPException(status_code=400, detail="В цикле нет обратной связи для правки")
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/cabinet/feedback/message/{message_id}/edit")
def edit_feedback_message(
    message_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    background_tasks: BackgroundTasks,
    text: str = Form(default=""),
):
    """Автор сообщения ОС правит ТЕКСТ своего сообщения, пока цикл «на изменении».

    Право: можно править только своё сообщение (sender_id == текущий) и только
    когда SA вернул цикл (revision_requested_at установлен). Студента не пускаем.
    Правка текстовая; фото не трогаем. Ученику шлём уведомление перечитать ОС.
    """
    from app.models.notification import Notification

    msg = db.query(FeedbackMessage).filter(FeedbackMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    if msg.sender_role == fb_service.ROLE_STUDENT:
        raise HTTPException(status_code=403, detail="Сообщения ученика не редактируются")
    if msg.sender_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Можно править только своё сообщение")

    fb = db.query(Feedback).filter(Feedback.id == msg.feedback_id).first()
    work = db.query(Work).filter(Work.id == fb.work_id).first() if fb else None
    cycle = (
        db.query(ExamCycle).filter(ExamCycle.id == work.cycle_id).first()
        if work and work.cycle_id else None
    )
    if cycle is None:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    if not cycle.is_on_revision:
        raise HTTPException(
            status_code=403,
            detail="Правка доступна только когда суперадмин вернул цикл на изменение",
        )

    text_clean = (text or "").strip()
    if not text_clean:
        raise HTTPException(status_code=400, detail="Текст сообщения не может быть пустым")
    if len(text_clean) > MAX_TEXT_LEN:
        text_clean = text_clean[:MAX_TEXT_LEN]
    msg.text = text_clean

    notification = Notification(
        user_id=work.user_id,
        title="Куратор обновил обратную связь",
        text=f"По работе #{work.id} ({work.subject or ''}) обратная связь была изменена.",
        work_id=work.id,
    )
    db.add(notification)
    db.commit()
    invalidate_unread(work.user_id)
    background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True, "text": msg.text})


@router.post("/cabinet/feedback/{cycle_id}/revision-done")
def finish_revision_route(
    cycle_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Куратор завершает правку — снимает флаг «на изменении» (цикл остаётся закрыт)."""
    cycle = db.query(ExamCycle).filter(ExamCycle.id == cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    get_student_for_staff_access(
        db,
        user,
        cycle.user_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Это не ваш студент",
    )
    if not finish_curator_revision(db, cycle):
        raise HTTPException(status_code=400, detail="Цикл не на изменении")
    db.commit()
    return JSONResponse({"ok": True})


# ── Staff: календарь работ конкретного ученика (legacy, для карточки) ────────

def _ensure_staff_can_view_student(db: DBSession, user: dict, student_id: int) -> User:
    return get_student_for_staff_access(
        db,
        user,
        student_id,
        exclude_deleted=True,
        not_found_detail="Студент не найден",
        forbidden_detail="Не ваш студент",
    )


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
