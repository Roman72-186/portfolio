"""Admin and superadmin management endpoints for learning videos."""

import json
import logging
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header, require_superadmin
from app.models.audit_log import AuditLog
from app.models.exam_assignment import ExamAssignment
from app.models.learning_video import LearningVideo
from app.services.bunny_stream import (
    BunnyStreamAPIError,
    BunnyStreamCreateUncertainError,
    BunnyStreamConfigError,
    build_tus_credentials,
    create_video,
    delete_video,
    get_video,
    is_bunny_upload_available,
    normalize_bunny_status,
)
from app.services.video_catalog import list_all_videos, publish_video, unpublish_video
from app.tmpl import templates


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cabinet/admin/videos")

ALLOWED_VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".vod",
    ".flv", ".wmv", ".ts", ".amv", ".mpeg",
}
MAX_VIDEO_SIZE_BYTES = 50 * 1024 * 1024 * 1024


class CreateVideoUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=MAX_VIDEO_SIZE_BYTES)
    mime_type: str = Field(min_length=1, max_length=100)

    @field_validator("title", "filename")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if PurePath(value).name != value:
            raise ValueError("Filename must not contain a path")
        if PurePath(value).suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            raise ValueError("Unsupported video format")
        return value

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, value: str) -> str:
        value = value.strip().lower()
        if not (value.startswith("video/") or value == "application/octet-stream"):
            raise ValueError("Unsupported video MIME type")
        return value


class UpdateVideoMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    # None — урок остаётся открытым всем ученикам; id — доступ идёт по теме.
    assignment_id: int | None = Field(default=None, ge=1)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("description")
    @classmethod
    def strip_optional_description(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class DeleteVideoConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(min_length=1, max_length=200)


def _get_video_or_404(db: DBSession, video_id: int) -> LearningVideo:
    video = db.get(LearningVideo, video_id)
    if not video or video.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Видео не найдено")
    return video


def _audit(db: DBSession, *, action: str, user_id: int, video: LearningVideo) -> None:
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(
                {"video_id": video.id, "bunny_video_id": video.bunny_video_id, "title": video.title[:200]},
                ensure_ascii=False,
            ),
        )
    )


def _serialize_video(video: LearningVideo) -> dict:
    return {
        "id": video.id,
        "status": video.status,
        "bunny_status": video.bunny_status,
        "encode_progress": video.encode_progress,
        "duration_seconds": video.duration_seconds,
        "is_published": video.is_published,
        "status_message": video.status_message,
        "assignment_id": video.assignment_id,
    }


@router.get("", response_class=HTMLResponse)
def video_admin_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    assignments = (
        db.query(ExamAssignment)
        .filter(ExamAssignment.status != "archived")
        .order_by(ExamAssignment.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "cabinet_videos_admin.html",
        {
            "request": request,
            "user": user,
            "videos": list_all_videos(db),
            "assignments": assignments,
            "assignment_titles": {a.id: a.title for a in assignments},
            "upload_available": is_bunny_upload_available(),
        },
    )


@router.post("/create-upload", response_class=JSONResponse)
def create_video_upload(
    payload: CreateVideoUpload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    if not settings.bunny_stream_enabled or not is_bunny_upload_available():
        return JSONResponse({"ok": False, "error": "upload_not_configured"}, status_code=503)

    try:
        bunny_video = create_video(payload.title)
        bunny_video_id = str(bunny_video["guid"])
        next_order = (db.query(func.max(LearningVideo.sort_order)).scalar() or 0) + 1
        video = LearningVideo(
            bunny_library_id=settings.bunny_stream_library_id,
            bunny_video_id=bunny_video_id,
            title=payload.title,
            description=payload.description,
            original_filename=payload.filename,
            original_size_bytes=payload.size_bytes,
            original_mime_type=payload.mime_type,
            sort_order=next_order,
            status="uploading",
            bunny_status=bunny_video.get("status"),
            encode_progress=bunny_video.get("encodeProgress"),
            created_by_id=user["user_id"],
        )
        db.add(video)
        db.flush()
        _audit(db, action="video_create", user_id=user["user_id"], video=video)
        db.commit()
        db.refresh(video)
    except BunnyStreamCreateUncertainError:
        logger.exception("Bunny video create result is uncertain; manual provider check required")
        return JSONResponse({"ok": False, "error": "provider_create_uncertain"}, status_code=504)
    except (BunnyStreamAPIError, BunnyStreamConfigError, KeyError):
        logger.exception("Bunny video object creation failed")
        return JSONResponse({"ok": False, "error": "provider_create_failed"}, status_code=502)
    except SQLAlchemyError:
        logger.exception("Local video catalogue insert failed for Bunny video_id=%s", locals().get("bunny_video_id"))
        db.rollback()
        if "bunny_video_id" in locals():
            try:
                delete_video(bunny_video_id)
            except (BunnyStreamAPIError, BunnyStreamConfigError):
                logger.exception("Failed to clean up orphan Bunny video_id=%s", bunny_video_id)
        return JSONResponse({"ok": False, "error": "catalogue_create_failed"}, status_code=503)

    credentials = build_tus_credentials(video.bunny_video_id)
    return JSONResponse({"ok": True, "catalog_video_id": video.id, **credentials})


@router.post("/{video_id}/refresh", response_class=JSONResponse)
def refresh_video_status(
    video_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    video = _get_video_or_404(db, video_id)
    try:
        remote = get_video(video.bunny_video_id)
    except (BunnyStreamAPIError, BunnyStreamConfigError):
        logger.exception("Bunny video status refresh failed for video_id=%s", video.id)
        return JSONResponse({"ok": False, "error": "provider_status_failed"}, status_code=502)

    bunny_status = remote.get("status")
    video.bunny_status = bunny_status if isinstance(bunny_status, int) else None
    video.status = normalize_bunny_status(video.bunny_status)
    encode_progress = remote.get("encodeProgress")
    video.encode_progress = max(0, min(100, int(encode_progress))) if isinstance(encode_progress, (int, float)) else None
    duration = remote.get("length")
    video.duration_seconds = float(duration) if isinstance(duration, (int, float)) and duration >= 0 else None
    messages = remote.get("transcodingMessages") or []
    video.status_message = str(messages[-1].get("message", ""))[:500] or None if messages else None
    db.commit()
    db.refresh(video)
    return JSONResponse({"ok": True, "video": _serialize_video(video)})


@router.post("/{video_id}/upload-credentials", response_class=JSONResponse)
def renew_video_upload_credentials(
    video_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Renew a presign so the same browser file can resume after a reload."""
    video = _get_video_or_404(db, video_id)
    if video.status != "uploading":
        return JSONResponse({"ok": False, "error": "upload_not_resumable"}, status_code=409)
    try:
        credentials = build_tus_credentials(video.bunny_video_id)
    except BunnyStreamConfigError:
        return JSONResponse({"ok": False, "error": "upload_not_configured"}, status_code=503)
    return JSONResponse({"ok": True, "catalog_video_id": video.id, **credentials})


@router.post("/{video_id}/metadata", response_class=JSONResponse)
def update_video_metadata(
    video_id: int,
    payload: UpdateVideoMetadata,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    video = _get_video_or_404(db, video_id)
    if payload.assignment_id is not None and not db.get(ExamAssignment, payload.assignment_id):
        return JSONResponse({"ok": False, "error": "assignment_not_found"}, status_code=422)
    video.title = payload.title
    video.description = payload.description
    video.assignment_id = payload.assignment_id
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{video_id}/publish", response_class=JSONResponse)
def publish_catalog_video(
    video_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    video = _get_video_or_404(db, video_id)
    try:
        publish_video(video, user_id=user["user_id"])
    except ValueError:
        return JSONResponse({"ok": False, "error": "video_not_ready"}, status_code=409)
    _audit(db, action="video_publish", user_id=user["user_id"], video=video)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{video_id}/unpublish", response_class=JSONResponse)
def unpublish_catalog_video(
    video_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    video = _get_video_or_404(db, video_id)
    unpublish_video(video)
    _audit(db, action="video_unpublish", user_id=user["user_id"], video=video)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{video_id}/delete", response_class=JSONResponse)
def delete_catalog_video(
    video_id: int,
    payload: DeleteVideoConfirmation,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    video = _get_video_or_404(db, video_id)
    if video.is_published:
        return JSONResponse({"ok": False, "error": "unpublish_first"}, status_code=409)
    if payload.confirmation.strip() != video.title:
        return JSONResponse({"ok": False, "error": "confirmation_mismatch"}, status_code=422)

    video.status = "deleting"
    db.commit()
    try:
        delete_video(video.bunny_video_id)
    except (BunnyStreamAPIError, BunnyStreamConfigError):
        logger.exception("Bunny video deletion failed for video_id=%s", video.id)
        video.status = "delete_failed"
        _audit(db, action="video_delete_failed", user_id=user["user_id"], video=video)
        db.commit()
        return JSONResponse({"ok": False, "error": "provider_delete_failed"}, status_code=502)

    from datetime import datetime, timezone

    video.status = "deleted"
    video.deleted_at = datetime.now(timezone.utc)
    video.deleted_by_id = user["user_id"]
    _audit(db, action="video_delete", user_id=user["user_id"], video=video)
    db.commit()
    return JSONResponse({"ok": True})
