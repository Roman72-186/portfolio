"""Поворот фото на 90° из просмотрщика (`partials/lightbox.html`).

До 04.10.2026 роут жил в `cabinet_superadmin.py` и пускал только суперадмина.
Теперь поворот есть у всех ролей, каждой в своей зоне; кто какой файл может
крутить, решает `services/photo_rotation.py::can_rotate`. Адрес прежний.
"""

import asyncio
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import get_current_user, require_csrf, require_csrf_header
from app.services import photo_rotation
from app.services import s3 as s3_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cabinet")

# Столько ссылок проверяем за раз: галерея ученика открывает в просмотрщике
# все фото раздела, больше на одном экране не бывает.
MAX_ALLOWED_CHECK = 60


@router.post("/rotate-photo")
async def rotate_photo(
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    src: Annotated[str, Form()],
    direction: Annotated[str, Form()],
):
    if direction not in ("left", "right"):
        return JSONResponse({"success": False, "error": "Неверное направление поворота"}, status_code=422)
    if not s3_service.is_configured():
        return JSONResponse({"success": False, "error": "S3 не настроен"}, status_code=503)

    s3_path = photo_rotation.s3_path_from_src(src)
    if not s3_path:
        return JSONResponse({"success": False, "error": "Неизвестный файл"}, status_code=400)
    if not photo_rotation.can_rotate(db, user, s3_path):
        return JSONResponse({"success": False, "error": "Это фото повернуть нельзя"}, status_code=403)

    work = photo_rotation.work_with_thumb(db, s3_path)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: photo_rotation.rotate_in_storage(
            s3_path, clockwise=(direction == "right"), rebuild_thumb=work is not None,
        ),
    )
    if result.error:
        return JSONResponse({"success": False, "error": result.error}, status_code=422)

    version = int(time.time())
    thumb_src = None
    if work is not None:
        # Превью не пересобралось — пусть квадратик берёт само фото, а не
        # показывает старую ориентацию.
        thumb_src = f"{result.thumb_url}?v={version}" if result.thumb_url else None
        work.thumb_s3_url = thumb_src
        db.commit()

    logger.info("rotate-photo by %s: %s (%s)", user.get("user_id"), s3_path, direction)
    return JSONResponse({"success": True, "src": f"{result.url}?v={version}", "thumb_src": thumb_src})


@router.post("/rotate-photo/allowed")
def rotate_photo_allowed(
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
    srcs: Annotated[list[str], Body(embed=True)],
):
    """Какие из ссылок просмотрщика этот пользователь может повернуть.

    Один запрос на открытие просмотрщика: кнопки ⟲ ⟳ показываются только у
    разрешённых слайдов, а не у всех подряд с отказом после нажатия.
    """
    allowed = []
    for src in srcs[:MAX_ALLOWED_CHECK]:
        s3_path = photo_rotation.s3_path_from_src(src)
        if s3_path and photo_rotation.can_rotate(db, user, s3_path):
            allowed.append(src)
    return {"allowed": allowed}
