"""Экран загрузки голосовых для уведомления по уровню точки А (Фаза 1,
владелец 17.09.2026). Две карточки — уровень 1 и уровень 2 (см. порог в
`app/services/point_a.py::POINT_A_LEVEL_1_MIN_AVERAGE`), каждая с текущим
файлом, плеером и формой замены. Само уведомление это сюда не заходит —
только читает `PointALevelAudio` через `point_a_level_audio.get_level_audio`.

Лимит размера — свой, строже общего голосового ОС (`feedback.py`,
25 МБ): Telegram Bot API `sendVoice` берёт файл по URL, и владелец 17.09.2026
согласовал ~15 МБ с запасом от лимита Telegram в 20 МБ на файл по URL.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf
from app.services import feedback as fb_service
from app.services.point_a_level_audio import get_level_audio, upsert_level_audio
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/point-a-audio")

MAX_POINT_A_AUDIO_SIZE = 15 * 1024 * 1024
LEVELS = (1, 2)


@router.get("", response_class=HTMLResponse)
def point_a_audio_screen(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    audios = {level: get_level_audio(db, level) for level in LEVELS}
    return templates.TemplateResponse(request, "staff_point_a_audio.html", {
        "request": request,
        "user": user,
        "audios": audios,
        "nav_active": "point_a",
    })


@router.post("/{level}")
async def point_a_audio_upload(
    level: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    audio: UploadFile = File(...),
):
    if level not in LEVELS:
        raise HTTPException(status_code=404, detail="Такого уровня нет")
    if not audio.filename:
        raise HTTPException(status_code=422, detail="Выберите файл")

    ext = Path(audio.filename).suffix.lower()
    content_type = (audio.content_type or "").lower()
    if (
        content_type not in fb_service.ALLOWED_FEEDBACK_AUDIO_TYPES
        and ext not in fb_service.ALLOWED_FEEDBACK_AUDIO_EXTENSIONS
    ):
        raise HTTPException(
            status_code=422,
            detail="Голосовое должно быть в формате mp3, ogg, opus, webm, wav, m4a, aac, amr или 3gp",
        )

    data = await audio.read(MAX_POINT_A_AUDIO_SIZE + 1)
    if len(data) > MAX_POINT_A_AUDIO_SIZE:
        raise HTTPException(status_code=413, detail="Голосовое больше 15 МБ")
    if not data:
        raise HTTPException(status_code=422, detail="Файл пустой")

    saved = upsert_level_audio(
        db,
        level=level,
        filename=audio.filename,
        data=data,
        content_type=content_type or "audio/mpeg",
        uploaded_by_id=user["user_id"],
    )
    if saved is None:
        raise HTTPException(status_code=502, detail="Не удалось загрузить файл в хранилище")
    db.commit()

    return RedirectResponse("/cabinet/staff/point-a-audio?ok=1", status_code=302)
