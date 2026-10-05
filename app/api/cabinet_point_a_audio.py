"""Экран голосовых для уведомления по уровню точки А (Фаза 1, владелец
17.09.2026). Две карточки — уровень 1 и уровень 2 (см. порог в
`app/services/point_a.py::POINT_A_LEVEL_2_MIN_AVERAGE`), каждая с текущим
файлом, плеером и формой замены. Само уведомление сюда не заходит — только
читает `PointALevelAudio` через `point_a_level_audio.get_level_audio`.

Голосовое можно записать прямо на экране (`media-recorder-field.js`, владелец
29.09.2026: «ГП может загрузить или записать аудио») или прикрепить файлом.
До S3 любой вход перегоняется в m4a (`media_transcode.playable_voice`):
браузерный webm iPhone не доигрывает (`docs/invariants/video-media.md`).

Лимит — общий для голосовых, 25 МБ (`feedback.read_audio_upload`). Прежние
15 МБ держались ради `sendVoice` по ссылке; с 29.09.2026 Telegram получает сам
файл (`notify._send_telegram`), и ссылочный лимит его не касается.
"""
from __future__ import annotations

import asyncio
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf
from app.services import feedback as fb_service, media_transcode
from app.services.point_a import POINT_A_LEVEL_2_MIN_AVERAGE
from app.services.point_a_level_audio import get_level_audio, upsert_level_audio
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/point-a-audio")

MAX_POINT_A_AUDIO_SIZE = fb_service.MAX_FEEDBACK_AUDIO_SIZE
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
        # Подписи «N и ниже / N и выше» — из того же порога, что считает
        # уровень: цифры в шаблоне разъехались бы с ним при следующем сдвиге.
        "level_2_min": POINT_A_LEVEL_2_MIN_AVERAGE,
        "nav_active": "point_a",
    })


@router.post("/{level}")
async def point_a_audio_upload(
    level: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    # Необязательный: пустое «Сохранить» даёт FastAPI пустую часть формы, и
    # при `File(...)` человек увидел бы сырой JSON 422 вместо текста у формы.
    audio: UploadFile | None = File(None),
):
    if level not in LEVELS:
        raise HTTPException(status_code=404, detail="Такого уровня нет")

    # Отклонённый файл (плохой формат, размер, пустышка) — это ошибка
    # пользователя на обычной HTML-форме без JS, не fetch. Голый
    # HTTPException(422/413) отдаёт браузеру сырой JSON вместо страницы —
    # владелец 17.09.2026 попросил вместо этого текст рядом с формой.
    def rejected(message: str) -> RedirectResponse:
        return RedirectResponse(
            f"/cabinet/staff/point-a-audio?error={quote(message)}&level={level}",
            status_code=302,
        )

    try:
        upload = await fb_service.read_audio_upload(audio, max_size=MAX_POINT_A_AUDIO_SIZE)
    except ValueError as exc:
        return rejected(str(exc))
    if upload is None:
        return rejected("Запишите голосовое или прикрепите файл")
    filename, data, content_type = await asyncio.to_thread(
        media_transcode.playable_voice, *upload,
    )

    saved = upsert_level_audio(
        db,
        level=level,
        filename=filename,
        data=data,
        content_type=content_type,
        uploaded_by_id=user["user_id"],
    )
    if saved is None:
        raise HTTPException(status_code=502, detail="Не удалось загрузить файл в хранилище")
    db.commit()

    return RedirectResponse(f"/cabinet/staff/point-a-audio?ok={level}", status_code=302)
