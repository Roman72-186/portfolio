"""Protected learning-video catalogue, playback and progress routes."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_learning_content_access
from app.models.learning_video import LearningVideo
from app.services.bunny_stream import BunnyStreamConfigError, build_signed_embed_url
from app.services.video_catalog import (
    get_published_video,
    legacy_pilot_video,
    list_published_videos,
)
from app.services.video_progress import (
    get_resume_position,
    get_video_progress,
    save_video_progress as persist_video_progress,
)
from app.tmpl import templates


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cabinet")


class VideoProgressUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_seconds: float = Field(ge=0, le=604_800, allow_inf_nan=False)
    duration_seconds: float | None = Field(default=None, gt=0, le=604_800, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_position(self):
        if self.duration_seconds is not None and self.position_seconds > self.duration_seconds + 5:
            raise ValueError("position_seconds cannot exceed duration_seconds")
        return self


def _not_found(request: Request, user: dict):
    return templates.TemplateResponse("404.html", {"request": request, "user": user}, status_code=404)


def _video_for_viewer(db: DBSession, *, catalog_id: int, user: dict):
    if not settings.bunny_stream_enabled:
        return None
    video = get_published_video(db, catalog_id)
    if video is not None:
        return video
    if user.get("role_rank", 0) < 4:
        return None
    candidate = db.get(LearningVideo, catalog_id)
    if candidate and candidate.deleted_at is None and candidate.status == "ready":
        return candidate
    return None


def _render_player(
    request: Request,
    user: dict,
    db: DBSession,
    *,
    video,
    progress_endpoint: str,
):
    viewer_name = " ".join(
        str(user.get(field) or "").strip()
        for field in ("first_name", "last_name")
    ).strip()
    if not viewer_name:
        viewer_name = str(user.get("name") or "").strip() or "Имя не указано"

    viewer_username = str(user.get("tg_username") or "").strip().lstrip("@")
    viewer_phone = str(user.get("phone") or "").strip()
    context = {
        "request": request,
        "user": user,
        "video_title": video.title,
        "video_description": getattr(video, "description", None),
        "progress_endpoint": progress_endpoint,
        "back_url": "/cabinet/admin/videos" if user.get("role_rank", 0) >= 4 else "/cabinet/videos",
        "viewer_watermark": {
            "name": viewer_name,
            "username": f"@{viewer_username}" if viewer_username else "Username не указан",
            "phone": viewer_phone or "Телефон не указан",
        },
    }
    try:
        context["player_url"] = build_signed_embed_url(video.bunny_video_id)
    except BunnyStreamConfigError as exc:
        logger.error("Bunny Stream playback configuration error: %s", exc)
        context["player_url"] = None
        return templates.TemplateResponse("cabinet_video.html", context, status_code=503)

    try:
        progress = get_video_progress(db, user_id=user["user_id"], video_id=video.bunny_video_id)
        context["resume_position_seconds"] = get_resume_position(progress)
    except SQLAlchemyError:
        logger.exception("Video progress read failed for user_id=%s", user["user_id"])
        db.rollback()
        context["resume_position_seconds"] = 0.0
    return templates.TemplateResponse("cabinet_video.html", context)


@router.get("/videos", response_class=HTMLResponse)
def cabinet_videos(
    request: Request,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
):
    items = []
    for video in list_published_videos(db):
        try:
            progress = get_video_progress(db, user_id=user["user_id"], video_id=video.bunny_video_id)
        except SQLAlchemyError:
            logger.exception("Video catalogue progress read failed for user_id=%s", user["user_id"])
            db.rollback()
            progress = None
        resume = get_resume_position(progress)
        state = "completed" if progress and progress.completed_at else ("started" if resume >= 5 else "new")
        items.append({"video": video, "resume_seconds": resume, "state": state})
    return templates.TemplateResponse(
        "cabinet_videos.html", {"request": request, "user": user, "items": items}
    )


@router.get("/videos/{video_id}", response_class=HTMLResponse)
def cabinet_video_by_id(
    video_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
):
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return _not_found(request, user)
    return _render_player(
        request, user, db, video=video, progress_endpoint=f"/cabinet/videos/{video_id}/progress"
    )


@router.post("/videos/{video_id}/progress", response_class=JSONResponse)
def save_catalog_video_progress(
    video_id: int,
    payload: VideoProgressUpdate,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        raise HTTPException(status_code=404, detail="Видео не найдено")
    return _save_progress(payload, user=user, db=db, bunny_video_id=video.bunny_video_id)


@router.get("/video", response_class=HTMLResponse)
def cabinet_video_legacy(
    request: Request,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if not settings.bunny_stream_enabled:
        return _not_found(request, user)
    catalog_video = (
        db.query(LearningVideo)
        .filter(
            LearningVideo.bunny_video_id == settings.bunny_stream_video_id,
            LearningVideo.deleted_at.is_(None),
            LearningVideo.is_published.is_(True),
            LearningVideo.status == "ready",
        )
        .first()
    )
    if catalog_video:
        return RedirectResponse(f"/cabinet/videos/{catalog_video.id}", status_code=302)
    if db.query(LearningVideo.id).first():
        return _not_found(request, user)
    return _render_player(
        request,
        user,
        db,
        video=legacy_pilot_video(),
        progress_endpoint="/cabinet/video/progress",
    )


def _save_progress(
    payload: VideoProgressUpdate,
    *,
    user: dict,
    db: DBSession,
    bunny_video_id: str,
):
    completed = (
        payload.duration_seconds is not None
        and payload.duration_seconds - payload.position_seconds <= 5
    )
    try:
        completed = persist_video_progress(
            db,
            user_id=user["user_id"],
            video_id=bunny_video_id,
            position_seconds=payload.position_seconds,
            duration_seconds=payload.duration_seconds,
            completed=completed,
        )
    except SQLAlchemyError:
        logger.exception("Video progress save failed for user_id=%s", user["user_id"])
        db.rollback()
        return JSONResponse({"ok": False, "error": "save_failed"}, status_code=503)
    return JSONResponse({"ok": True, "completed": completed})


@router.post("/video/progress", response_class=JSONResponse)
def save_video_progress_legacy(
    payload: VideoProgressUpdate,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    if not settings.bunny_stream_enabled:
        return JSONResponse({"ok": False, "error": "video_disabled"}, status_code=404)
    return _save_progress(
        payload, user=user, db=db, bunny_video_id=settings.bunny_stream_video_id
    )
