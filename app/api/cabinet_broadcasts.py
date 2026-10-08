"""Экран «Рассылки» — сообщения ученикам от преподавателя через бота
(владелец 07.10.2026, план `plans/2026-10-07-apparchi-сообщения-лизы-я-с-вами.md`).

Порядок для человека: собрать сообщение (текст в оформлении Telegram, фото,
голосовое или кружок) → выбрать, кому (тарифы, уровень точки А, поимённо) →
«Прислать мне на проверку»: бот присылает сообщение ровно таким, каким его
получат ученики, под ним «Отправить N ученикам» и «Исправить» → отправить
здесь или кнопкой в Telegram → журнал по ученикам.

Правила — `services/broadcasts.py`, сеть — `services/broadcast_delivery.py`.
Доступ — только суперадмин (владелец 08.10.2026: «только для СА, чтобы я
видел и тестил самостоятельно»). Раздел `broadcasts` ни одной роли не
положен; Лизе его открывает суперадмин в «Доступах» — внутри открытого
раздела ранг поднимается до `Section.min_rank` (5), выкатка не нужна.
"""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import tariff_choices
from app.db.database import get_db
from app.dependencies import require_csrf, require_superadmin
from app.models.broadcast import (
    MEDIA_PHOTO,
    MEDIA_VIDEO_NOTE,
    MEDIA_VOICE,
    STATUS_DRAFT,
    Broadcast,
    BroadcastRecipient,
)
from app.models.user import User
from app.services import broadcast_delivery, media_transcode
from app.services import broadcasts as bc
from app.services import feedback as fb_service
from app.services import s3 as s3_service
from app.services.point_a import POINT_A_LEVEL_2_MIN_AVERAGE
from app.services.upload_validation import is_allowed_image
from app.services.utils import compress_image
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/broadcasts")

BASE = "/cabinet/staff/broadcasts"
# Кружок в Telegram — до минуты, на входе больше и не нужно: браузер пишет
# минуту (`media-recorder-field.js`), файл с телефона режется до 60 секунд.
MAX_NOTE_INPUT_SIZE = 100 * 1024 * 1024
MAX_PHOTO_INPUT_SIZE = fb_service.MAX_FEEDBACK_PHOTO_INPUT_SIZE


def _redirect(broadcast_id: int | None, **params: str) -> RedirectResponse:
    url = f"{BASE}/{broadcast_id}" if broadcast_id else BASE
    if params:
        url += "?" + "&".join(f"{key}={quote(value)}" for key, value in params.items())
    return RedirectResponse(url, status_code=302)


def _get_broadcast(db: DBSession, broadcast_id: int) -> Broadcast:
    broadcast = db.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="Рассылки нет")
    return broadcast


def _student_names(db: DBSession, user_ids) -> dict[int, str]:
    if not user_ids:
        return {}
    return {
        user.id: " ".join(p for p in (user.first_name, user.last_name) if p) or f"ученик {user.id}"
        for user in db.query(User).filter(User.id.in_(list(user_ids)))
    }


def _plain_title(broadcast: Broadcast) -> str:
    """Первая строка текста — заголовок карточки в журнале."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", broadcast.text or "")).strip()
    first = plain.split("\n", 1)[0].strip()
    if first:
        return first if len(first) <= 80 else first[:79] + "…"
    if broadcast.media_kind:
        return bc.MEDIA_LABELS[broadcast.media_kind]
    return "Пустое сообщение"


@router.get("", response_class=HTMLResponse)
def broadcasts_list(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
):
    rows = []
    for broadcast in db.query(Broadcast).order_by(Broadcast.created_at.desc(), Broadcast.id.desc()).limit(200):
        audience = bc.get_audience(db, broadcast.id)
        rows.append({
            "broadcast": broadcast,
            "title": _plain_title(broadcast),
            "audience": bc.audience_text(audience),
            "counts": bc.recipient_counts(db, broadcast.id) if broadcast.status != STATUS_DRAFT else None,
        })
    return templates.TemplateResponse(request, "staff_broadcasts.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "media_labels": bc.MEDIA_LABELS,
        "nav_active": "broadcasts",
    })


@router.post("")
def broadcast_create(
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    broadcast = Broadcast(created_by_id=user["user_id"], text="")
    db.add(broadcast)
    db.commit()
    return _redirect(broadcast.id)


@router.get("/audience")
def broadcast_audience(
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    tariffs: Annotated[list[str], Query()] = [],
    levels: Annotated[list[str], Query()] = [],
    students: str = "",
):
    """Живой счётчик под выбором: сколько получат и сколько не дойдёт."""
    audience = bc.normalize_audience(
        db, tariffs=tariffs, levels=levels, user_ids=[s for s in students.split(",") if s],
    )
    summary = bc.summarize(bc.audience_users(db, audience))
    return JSONResponse({
        "total": summary.total,
        "reachable": summary.reachable,
        "no_telegram": summary.no_telegram,
        "notifications_off": summary.notifications_off,
    })


@router.get("/{broadcast_id}", response_class=HTMLResponse)
def broadcast_screen(
    broadcast_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
):
    broadcast = _get_broadcast(db, broadcast_id)
    audience = bc.get_audience(db, broadcast.id)
    context = {
        "request": request,
        "user": user,
        "broadcast": broadcast,
        "text_html": bc.telegram_html_for_page(broadcast.text),
        "audience": audience,
        "audience_text": bc.audience_text(audience, _student_names(db, audience.user_ids)),
        "media_labels": bc.MEDIA_LABELS,
        "nav_active": "broadcasts",
    }
    if broadcast.status != STATUS_DRAFT:
        rows = (
            db.query(BroadcastRecipient, User)
            .join(User, User.id == BroadcastRecipient.user_id)
            .filter(BroadcastRecipient.broadcast_id == broadcast.id)
            .order_by(User.last_name, User.first_name, User.id)
            .all()
        )
        context.update({
            "recipients": [
                {
                    "name": " ".join(p for p in (u.last_name, u.first_name) if p) or f"Ученик {u.id}",
                    "username": (u.tg_username or "").strip().lstrip("@"),
                    "tariff": u.tariff or "",
                    "status": r.status,
                    "status_label": bc.RECIPIENT_LABELS.get(r.status, r.status),
                    "error": r.error or "",
                }
                for r, u in rows
            ],
            "counts": bc.recipient_counts(db, broadcast.id),
            "labels": bc.RECIPIENT_LABELS,
            "can_resume": bc.can_resume(broadcast),
        })
        return templates.TemplateResponse(request, "staff_broadcast_report.html", context)

    summary = bc.summarize(bc.audience_users(db, audience))
    context.update({
        "tariffs": tariff_choices(*audience.tariffs),
        "levels": bc.LEVELS,
        "point_a_level_2_min": POINT_A_LEVEL_2_MIN_AVERAGE,
        "students": bc.student_choices(db),
        "chosen_student_ids": sorted(audience.user_ids),
        "summary": summary,
        "preview_current": bc.preview_is_current(db, broadcast),
        "text_limit": bc.text_limit(broadcast.media_kind),
        "text_length": bc.visible_length(broadcast.text),
        "preview_choices": bc.preview_choices(db, user["user_id"]),
        "TEXT_LIMIT": bc.TEXT_LIMIT,
        "CAPTION_LIMIT": bc.CAPTION_LIMIT,
    })
    return templates.TemplateResponse(request, "staff_broadcast_edit.html", context)


async def _read_media(
    *, audio: UploadFile | None, video: UploadFile | None, photo: UploadFile | None,
) -> tuple[str, str, bytes, str] | None:
    """Вложение с формы → (вид, имя, байты, тип) уже в формате Telegram.

    Одно на сообщение: прислали два — ошибка, а не молчаливый выбор.
    ValueError — текст для человека.
    """
    present = [
        (kind, upload) for kind, upload in
        ((MEDIA_VIDEO_NOTE, video), (MEDIA_VOICE, audio), (MEDIA_PHOTO, photo))
        if upload is not None and upload.filename
    ]
    if not present:
        return None
    if len(present) > 1:
        raise ValueError("В сообщении может быть только одно вложение: фото, голосовое или кружок")
    kind, upload = present[0]
    if kind == MEDIA_VOICE:
        read = await fb_service.read_audio_upload(upload)
        if read is None:
            return None
        name, data, content_type = await asyncio.to_thread(media_transcode.telegram_voice, *read)
        return kind, name, data, content_type
    if kind == MEDIA_VIDEO_NOTE:
        read = await fb_service.read_video_upload(upload, max_size=MAX_NOTE_INPUT_SIZE)
        if read is None:
            return None
        name, data, content_type = await asyncio.to_thread(media_transcode.telegram_note, *read)
        return kind, name, data, content_type
    if not is_allowed_image(upload.content_type, upload.filename):
        raise ValueError("Фото должно быть в формате jpg, png, webp или heic")
    data = await upload.read(MAX_PHOTO_INPUT_SIZE + 1)
    if len(data) > MAX_PHOTO_INPUT_SIZE:
        raise ValueError(f"Фото больше {MAX_PHOTO_INPUT_SIZE // (1024 * 1024)} МБ")
    if not data:
        return None
    data = await asyncio.to_thread(compress_image, data)
    return kind, "photo.jpg", data, "image/jpeg"


@router.post("/{broadcast_id}")
async def broadcast_save(
    broadcast_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    text: Annotated[str, Form()] = "",
    tariffs: Annotated[list[str], Form()] = [],
    levels: Annotated[list[str], Form()] = [],
    student_ids: Annotated[str, Form()] = "",
    remove_media: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "save",
    preview_to: Annotated[str, Form()] = "",
    audio: UploadFile | None = File(None),
    video: UploadFile | None = File(None),
    photo: UploadFile | None = File(None),
):
    """Сохранить черновик; с `action=preview` — и прислать на проверку."""
    broadcast = _get_broadcast(db, broadcast_id)
    if broadcast.status != STATUS_DRAFT:
        return _redirect(broadcast_id, error="Отправленную рассылку уже не поправить")

    try:
        media = await _read_media(audio=audio, video=video, photo=photo)
    except ValueError as exc:
        return _redirect(broadcast_id, error=str(exc))

    old_path = broadcast.media_s3_path
    if media is not None:
        kind, name, data, content_type = media
        s3_path = s3_service.s3_path_broadcast_media(broadcast.id, name)
        url = await asyncio.to_thread(s3_service.upload_to_s3, s3_path, data, content_type)
        if not url:
            return _redirect(broadcast_id, error="Не удалось сохранить вложение в хранилище")
        broadcast.media_kind = kind
        broadcast.media_s3_path = s3_path
        broadcast.media_s3_url = url
        broadcast.media_filename = name
        broadcast.media_content_type = content_type
        broadcast.telegram_file_id = None
    elif remove_media == "1":
        broadcast.media_kind = None
        broadcast.media_s3_path = None
        broadcast.media_s3_url = None
        broadcast.media_filename = None
        broadcast.media_content_type = None
        broadcast.telegram_file_id = None

    cleaned = bc.clean_telegram_html(text)
    limit = bc.text_limit(broadcast.media_kind)
    length = bc.visible_length(cleaned)
    if length > limit:
        db.rollback()
        hint = " (подпись к фото и голосовому — до 1024)" if limit == bc.CAPTION_LIMIT else ""
        return _redirect(
            broadcast_id, error=f"Текст длиннее {limit} символов: сейчас {length}{hint}",
        )
    broadcast.text = cleaned

    audience = bc.normalize_audience(
        db, tariffs=tariffs, levels=levels,
        user_ids=[s for s in student_ids.split(",") if s.strip()],
    )
    bc.save_audience(db, broadcast, audience)
    db.flush()
    if broadcast.preview_fingerprint != bc.fingerprint(broadcast, audience):
        bc.invalidate_preview(broadcast)
    broadcast.updated_at = datetime.now(timezone.utc)
    db.commit()

    if old_path and old_path != broadcast.media_s3_path:
        await asyncio.to_thread(s3_service.delete_from_s3, old_path)

    if action != "preview":
        return _redirect(broadcast_id, ok="saved")
    target_id = int(preview_to) if preview_to.strip().isdigit() else None
    error = await broadcast_delivery.send_preview(broadcast_id, user["user_id"], target_id)
    if error:
        return _redirect(broadcast_id, error=error)
    return _redirect(broadcast_id, ok="preview")


@router.post("/{broadcast_id}/send")
def broadcast_send(
    broadcast_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    _get_broadcast(db, broadcast_id)
    try:
        bc.start_sending(db, broadcast_id, actor_id=user["user_id"])
    except bc.SendRefused as exc:
        return _redirect(broadcast_id, error=str(exc))
    background_tasks.add_task(broadcast_delivery.run_broadcast, broadcast_id)
    return _redirect(broadcast_id, ok="sending")


@router.post("/{broadcast_id}/resume")
def broadcast_resume(
    broadcast_id: int,
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Дослать «ждёт» после прерванного прохода (перезапуск приложения)."""
    _get_broadcast(db, broadcast_id)
    if not bc.claim_resume(db, broadcast_id):
        return _redirect(broadcast_id, error="Отправка ещё идёт — обновите страницу через пару минут")
    background_tasks.add_task(broadcast_delivery.run_broadcast, broadcast_id)
    return _redirect(broadcast_id, ok="sending")


@router.post("/{broadcast_id}/delete")
async def broadcast_delete(
    broadcast_id: int,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
):
    """Удалить черновик. Отправленное не удаляется — это журнал."""
    broadcast = _get_broadcast(db, broadcast_id)
    if broadcast.status != STATUS_DRAFT:
        return _redirect(broadcast_id, error="Отправленную рассылку удалить нельзя — это журнал")
    path = broadcast.media_s3_path
    db.delete(broadcast)
    db.commit()
    if path:
        await asyncio.to_thread(s3_service.delete_from_s3, path)
    return _redirect(None, ok="deleted")
