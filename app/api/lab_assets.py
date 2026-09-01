"""Отдача контента 3D-лаборатории с диска сервера — только вошедшим.

До 01.09.2026 модели, текстуры, превью и схемы браузер качал прямо из чужого
бакета TimeWeb. Бакет открытый: ссылка работала у кого угодно, без входа и без
связи с платформой. 01.09.2026 контент скопирован к нам (владелец разрешил), и
этот роут — единственная дверь к копии.

Каталог с файлами задаётся `LAB_ASSETS_DIR` и монтируется в контейнер только на
чтение. Внутри репозитория его нет: 4.8 ГБ в git не место, да и полный деплой
заливает ровно индекс git, так что мимо него каталог не поедет.

Условие доступа то же, что у самой страницы `/3dlab` в `api/auth.py`. Если оно
там меняется — менять и здесь, иначе картинка разойдётся со страницей.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.dependencies import get_current_user

router = APIRouter()

# mimetypes про эти расширения не знает, а без верного типа Three.js ругается на
# glTF, и <video> может отказаться играть.
_EXTRA_TYPES = {
    ".gltf": "model/gltf+json",
    ".glb": "model/gltf-binary",
    ".bin": "application/octet-stream",
}


def _resolve_asset(asset_path: str) -> Path:
    """Путь из запроса -> файл внутри каталога ассетов. Наружу не выпускает."""
    root = Path(settings.lab_assets_dir).resolve()

    # Пустое, абсолютное и с обратными слэшами отбрасываем до resolve():
    # `root / "/etc/passwd"` в pathlib даёт "/etc/passwd", а не путь внутри root.
    if not asset_path or asset_path.startswith("/") or "\\" in asset_path or "\x00" in asset_path:
        raise HTTPException(status_code=404, detail="Файл не найден")

    candidate = (root / asset_path).resolve()

    # Именно так ловится "..": сравниваем уже нормализованные пути.
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")

    return candidate


@router.get("/lab/asset/{asset_path:path}")
def lab_asset(
    asset_path: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Отдать файл лаборатории вошедшему пользователю.

    Без сессии `get_current_user` сам вернёт 401 — для fetch и <video> это
    правильнее, чем редирект на страницу входа, который они молча проглотят.
    """
    if not user.get("is_group_member") and not user.get("is_admin") and user.get("role_rank", 0) < 1:
        raise HTTPException(status_code=403, detail="Нет доступа к 3D-лаборатории")

    file_path = _resolve_asset(asset_path)
    suffix = file_path.suffix.lower()
    media_type = _EXTRA_TYPES.get(suffix) or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    # FileResponse сам ставит ETag, Last-Modified и умеет Range — перемотка видео
    # работает. `private` обязателен: файл персональный ровно настолько, насколько
    # персональна сессия, и общим кэшам его отдавать нельзя.
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )
