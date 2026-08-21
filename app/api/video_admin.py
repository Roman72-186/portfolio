"""Admin and superadmin management endpoints for learning videos."""

import json
import logging
from datetime import datetime, timezone
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
from app.models.learning_video import LearningVideo
from app.models.role import Role
from app.models.user import User
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
from app.services.tags import get_all_tags, parse_usernames
from app.services.tz import MSK_TZ
from app.services.video_catalog import list_all_videos, publish_video, unpublish_video
from app.services.video_topics import (
    ambiguous_tag_names,
    count_topic_audience,
    create_topic,
    delete_topic,
    get_assignee_ids,
    get_tag_ids,
    get_topic,
    list_topics,
    publish_topic,
    set_topic_assignees,
    set_topic_tags,
    unpublish_topic,
    update_topic,
)
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
    topic_id: int | None = Field(default=None, ge=1)

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


def _audit(
    db: DBSession,
    *,
    action: str,
    user_id: int,
    video: LearningVideo,
    extra: dict | None = None,
) -> None:
    details = {
        "video_id": video.id,
        "bunny_video_id": video.bunny_video_id,
        "title": video.title[:200],
    }
    if extra:
        details.update(extra)
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(details, ensure_ascii=False),
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
        "topic_id": video.topic_id,
    }


@router.get("", response_class=HTMLResponse)
def video_admin_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    topics = list_topics(db)
    topic_tag_ids = {t.id: get_tag_ids(db, t.id) for t in topics}
    topic_assignee_ids = {t.id: get_assignee_ids(db, t.id) for t in topics}
    all_tags = get_all_tags(db)
    ambiguous_names = set(ambiguous_tag_names(db, [tag.id for tag in all_tags]))
    return templates.TemplateResponse(
        "cabinet_videos_admin.html",
        {
            "request": request,
            "user": user,
            "videos": list_all_videos(db),
            "topics": topics,
            "topic_titles": {t.id: t.title for t in topics},
            "topic_tag_ids": topic_tag_ids,
            "topic_assignee_ids": topic_assignee_ids,
            # Время открытия форматируется здесь, а не в шаблоне: колонка
            # TIMESTAMPTZ приезжает в таймзоне сессии, а форма трактует ввод как
            # МСК — расхождение уводило бы тему на три часа за каждую правку.
            "topic_opens_form": {t.id: _format_msk_datetime_local(t.opens_at) for t in topics},
            "topic_opens_display": {t.id: _format_msk_display(t.opens_at) for t in topics},
            # Поимённые ученики возвращаются в форму, иначе сохранение стирает их.
            "topic_assignee_usernames": {
                t.id: _assignee_usernames(db, topic_assignee_ids.get(t.id, []))
                for t in topics
            },
            # Аудитория и спорные теги считаются на сервере, чтобы главный преподаватель видел
            # охват темы до того, как ученики не увидят урок.
            "topic_audience": {
                t.id: count_topic_audience(
                    db,
                    assign_to_all=t.assign_to_all,
                    tag_ids=topic_tag_ids.get(t.id, []),
                    assignee_ids=topic_assignee_ids.get(t.id, []),
                )
                for t in topics
            },
            "topic_ambiguous_tags": {
                t.id: ambiguous_tag_names(db, topic_tag_ids.get(t.id, []))
                for t in topics
            },
            "all_tags": all_tags,
            "ambiguous_tag_ids": {
                tag.id for tag in all_tags if tag.name in ambiguous_names
            },
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
    # Форме ответа провайдера не доверяем: NaN в числе или не-словарь в списке
    # сообщений дали бы 500 вместо аккуратного 502, хотя остальной маршрут отказы
    # Bunny обрабатывает. Статус при этом уже разобран и сохранён.
    encode_progress = remote.get("encodeProgress")
    try:
        video.encode_progress = (
            max(0, min(100, int(encode_progress)))
            if isinstance(encode_progress, (int, float))
            else None
        )
    except (ValueError, OverflowError):
        video.encode_progress = None
    duration = remote.get("length")
    try:
        video.duration_seconds = (
            float(duration) if isinstance(duration, (int, float)) and duration >= 0 else None
        )
    except (ValueError, OverflowError):
        video.duration_seconds = None
    messages = remote.get("transcodingMessages")
    last_message = messages[-1] if isinstance(messages, list) and messages else None
    if isinstance(last_message, dict):
        video.status_message = str(last_message.get("message", ""))[:500] or None
    else:
        video.status_message = None
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
    if payload.topic_id is not None and get_topic(db, payload.topic_id) is None:
        return JSONResponse({"ok": False, "error": "topic_not_found"}, status_code=422)
    previous_topic_id = video.topic_id
    video.title = payload.title
    video.description = payload.description
    video.topic_id = payload.topic_id
    # Маршрут меняет привязку к теме, то есть кто вообще увидит урок. Без записи
    # переброс урока на «доступно всем» (topic_id=None) не оставлял следа.
    _audit(
        db,
        action="video_metadata_update",
        user_id=user["user_id"],
        video=video,
        extra={"topic_id_before": previous_topic_id, "topic_id_after": payload.topic_id},
    )
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

    video.status = "deleted"
    video.deleted_at = datetime.now(timezone.utc)
    video.deleted_by_id = user["user_id"]
    _audit(db, action="video_delete", user_id=user["user_id"], video=video)
    db.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Темы недели
# ---------------------------------------------------------------------------

class TopicPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    # Локальное время МСК из <input type="datetime-local">, без таймзоны.
    opens_at: str = Field(min_length=1, max_length=32)
    assign_to_all: bool = False
    tag_ids: list[int] = Field(default_factory=list, max_length=200)
    # Поимённые исключения задаются списком @username — тем же способом, каким
    # владелец уже раздаёт теги (app/services/tags.py::parse_usernames).
    assignee_usernames: str = Field(default="", max_length=20_000)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


def _parse_opens_at(raw: str) -> datetime:
    """Строку из формы трактуем как московское время.

    В контейнере UTC, и без явной таймзоны тема открылась бы на три часа позже
    заявленного — та же ловушка, из-за которой в проекте всюду используется tz.py.
    """
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Неверная дата открытия") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MSK_TZ)
    return parsed


def _format_msk_datetime_local(value: datetime) -> str:
    """`opens_at` → строка для `<input type="datetime-local">` в МСК.

    Колонка `TIMESTAMPTZ`, и Postgres отдаёт её в таймзоне сессии — в контейнере
    UTC. Отдать это значение в форму как есть нельзя: `_parse_opens_at` трактует
    ввод как московское время, поэтому каждое повторное сохранение темы уводило
    бы её открытие на три часа назад. Формат тот же, что у пробников
    (`cabinet_superadmin.py`), — обе формы обязаны понимать время одинаково.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MSK_TZ).strftime("%Y-%m-%dT%H:%M")


def _format_msk_display(value: datetime) -> str:
    """`opens_at` → человекочитаемое московское время для списка тем."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MSK_TZ).strftime("%d.%m.%Y %H:%M")


def _resolve_assignees(db: DBSession, raw: str) -> tuple[list[int], list[str]]:
    """@username → id учеников. Возвращает найденных и ненайденных.

    `tg_username` зашифрован (EncryptedString), поэтому сравниваем в Python после
    расшифровки, а не через SQL — как в cabinet_tags.py::superadmin_bulk_lookup.
    """
    requested = parse_usernames(raw)
    if not requested:
        return [], []
    wanted = set(requested)
    student_role = db.query(Role).filter(Role.rank == 1).first()
    if student_role is None:
        return [], requested

    found: dict[str, int] = {}
    candidates = (
        db.query(User)
        .filter(
            User.role_id == student_role.id,
            User.is_active == True,  # noqa: E712
            User.deleted_at.is_(None),
        )
        .all()
    )
    for candidate in candidates:
        uname = (candidate.tg_username or "").strip().lstrip("@").lower()
        if uname in wanted and uname not in found:
            found[uname] = candidate.id
    not_found = [u for u in requested if u not in found]
    return list(found.values()), not_found


def _assignee_usernames(db: DBSession, user_ids: list[int]) -> str:
    """id учеников → строка «@user1, @user2» для предзаполнения формы.

    Без неё форма редактирования открывалась с пустым полем, а сохранение
    переписывало список поимённых целиком — любая правка темы снимала доступ у
    догоняющих. `tg_username` зашифрован, поэтому читаем объекты и расшифровываем
    в Python, как в `_resolve_assignees`.
    """
    if not user_ids:
        return ""
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    by_id = {u.id: (u.tg_username or "").strip().lstrip("@") for u in users}
    names = [by_id.get(uid, "") for uid in user_ids]
    return ", ".join(f"@{name}" for name in names if name)


def _audit_topic(db: DBSession, *, action: str, user_id: int, topic) -> None:
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(
                {"topic_id": topic.id, "title": topic.title[:200]}, ensure_ascii=False
            ),
        )
    )


def _topic_audience_feedback(
    db: DBSession, *, assign_to_all: bool, tag_ids: list[int], assignee_ids: list[int]
) -> dict:
    """Охват темы и спорные теги — чтобы главный преподаватель увидел промах адресации сразу."""
    return {
        "audience_size": count_topic_audience(
            db,
            assign_to_all=assign_to_all,
            tag_ids=tag_ids,
            assignee_ids=assignee_ids,
        ),
        "ambiguous_tags": ambiguous_tag_names(db, tag_ids),
    }


def _get_topic_or_404(db: DBSession, topic_id: int):
    topic = get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    return topic


@router.post("/topics", response_class=JSONResponse)
def create_video_topic(
    payload: TopicPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    topic = create_topic(
        db,
        title=payload.title,
        description=payload.description,
        opens_at=_parse_opens_at(payload.opens_at),
        assign_to_all=payload.assign_to_all,
        user_id=user["user_id"],
    )
    set_topic_tags(db, topic, payload.tag_ids)
    assignee_ids, not_found = _resolve_assignees(db, payload.assignee_usernames)
    set_topic_assignees(db, topic, assignee_ids)
    _audit_topic(db, action="video_topic_create", user_id=user["user_id"], topic=topic)
    feedback = _topic_audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse(
        {"ok": True, "topic_id": topic.id, "not_found": not_found, **feedback}
    )


@router.post("/topics/{topic_id}", response_class=JSONResponse)
def update_video_topic(
    topic_id: int,
    payload: TopicPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    topic = _get_topic_or_404(db, topic_id)
    update_topic(
        topic,
        title=payload.title,
        description=payload.description,
        opens_at=_parse_opens_at(payload.opens_at),
        assign_to_all=payload.assign_to_all,
        sort_order=payload.sort_order,
    )
    set_topic_tags(db, topic, payload.tag_ids)
    assignee_ids, not_found = _resolve_assignees(db, payload.assignee_usernames)
    set_topic_assignees(db, topic, assignee_ids)
    _audit_topic(db, action="video_topic_update", user_id=user["user_id"], topic=topic)
    feedback = _topic_audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse({"ok": True, "not_found": not_found, **feedback})


@router.post("/topics/{topic_id}/publish", response_class=JSONResponse)
def publish_video_topic(
    topic_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    topic = _get_topic_or_404(db, topic_id)
    try:
        publish_topic(topic, user_id=user["user_id"])
    except ValueError:
        return JSONResponse({"ok": False, "error": "topic_deleted"}, status_code=409)
    _audit_topic(db, action="video_topic_publish", user_id=user["user_id"], topic=topic)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/topics/{topic_id}/unpublish", response_class=JSONResponse)
def unpublish_video_topic(
    topic_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    topic = _get_topic_or_404(db, topic_id)
    unpublish_topic(topic)
    _audit_topic(db, action="video_topic_unpublish", user_id=user["user_id"], topic=topic)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/topics/{topic_id}/delete", response_class=JSONResponse)
def delete_video_topic(
    topic_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Мягкое удаление темы, только если к ней не привязано ни одного урока.

    Уроки при этом никуда не денутся, но и доступны не станут: `topic_id` у них
    сохраняется, а `accessible_topic_ids` удалённые темы отфильтровывает — то
    есть урок тихо исчезает у всех учеников, кроме персонала (fail-closed).
    `ON DELETE SET NULL` сработал бы только при физическом удалении, которого
    здесь нет. Поэтому гейт ниже защищает не от раздачи контента, а от потери
    доступа к нему: снимать его, рассчитывая на «ну станут открыты всем», нельзя.
    """
    topic = _get_topic_or_404(db, topic_id)
    attached = (
        db.query(LearningVideo.id)
        .filter(LearningVideo.topic_id == topic.id, LearningVideo.deleted_at.is_(None))
        .count()
    )
    if attached:
        return JSONResponse(
            {"ok": False, "error": "topic_has_videos", "videos": attached},
            status_code=409,
        )
    delete_topic(topic)
    _audit_topic(db, action="video_topic_delete", user_id=user["user_id"], topic=topic)
    db.commit()
    return JSONResponse({"ok": True})
