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
from app.models.tracker import ITEM_VIDEO, TrackerTask
from app.services.bunny_stream import BunnyStreamConfigError, build_signed_embed_url
from app.services.tracker import close_task_for_user
from app.services.video_catalog import (
    get_published_video,
    legacy_pilot_video,
    list_published_videos,
)
from app.services.video_progress import (
    get_resume_position,
    get_video_progress,
    log_video_view,
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
    video = get_published_video(db, catalog_id, viewer=user)
    if video is not None:
        return video
    if user.get("role_rank", 0) < 4:
        return None
    candidate = db.get(LearningVideo, catalog_id)
    if candidate and candidate.deleted_at is None and candidate.status == "ready":
        return candidate
    return None


def _player_url_payload(video) -> JSONResponse:
    """Свежая подписанная ссылка для уже открытой страницы.

    Токен Bunny живёт минуты, а страница живёт часами: при любом перезапросе
    iframe (возврат на вкладку, перезапуск PWA, старт просмотра позже TTL)
    старый URL отдаёт заглушку. Поднимать TTL нельзя — страница плеера
    открывается и без Referer, то есть утёкшая ссылка играет откуда угодно.
    """
    try:
        player_url = build_signed_embed_url(
            video.bunny_video_id, library_id=getattr(video, "bunny_library_id", None)
        )
    except BunnyStreamConfigError as exc:
        logger.error("Bunny Stream playback configuration error: %s", exc)
        return JSONResponse({"ok": False, "error": "player_unavailable"}, status_code=503)
    return JSONResponse(
        {
            "ok": True,
            "player_url": player_url,
            "ttl_seconds": settings.bunny_stream_token_ttl_seconds,
        }
    )


def _render_player(
    request: Request,
    user: dict,
    db: DBSession,
    *,
    video,
    progress_endpoint: str,
    player_url_endpoint: str,
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
        "player_url_endpoint": player_url_endpoint,
        "player_url_ttl_seconds": settings.bunny_stream_token_ttl_seconds,
        "back_url": "/cabinet/admin/videos" if user.get("role_rank", 0) >= 4 else "/cabinet/videos",
        "viewer_watermark": {
            "name": viewer_name,
            "username": f"@{viewer_username}" if viewer_username else "Username не указан",
            "phone": viewer_phone or "Телефон не указан",
        },
    }
    try:
        context["player_url"] = build_signed_embed_url(
            video.bunny_video_id, library_id=getattr(video, "bunny_library_id", None)
        )
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
    for video in list_published_videos(db, viewer=user):
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
        "cabinet_videos.html",
        {
            "request": request,
            "user": user,
            "items": items,
            # Персонал заходит в каталог из админки видео, и жёсткая ссылка на
            # ученический кабинет уводила его в чужой по смыслу экран. Правило то
            # же, что у страницы урока ниже. Ученик после трека A попадает на
            # /cabinet/learning — новую стартовую страницу, не на /cabinet/student
            # (роут жив, но ушёл из нижнего меню); куратор/модератор — как раньше.
            "back_url": (
                "/cabinet/admin/videos" if user.get("role_rank", 0) >= 4
                else "/cabinet/learning" if user.get("role_rank", 0) == 1
                else "/cabinet/student"
            ),
        },
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
    try:
        log_video_view(db, user_id=user["user_id"], video_id=video.bunny_video_id)
    except SQLAlchemyError:
        logger.exception("Video view log failed for user_id=%s", user["user_id"])
        db.rollback()
    return _render_player(
        request,
        user,
        db,
        video=video,
        progress_endpoint=f"/cabinet/videos/{video_id}/progress",
        player_url_endpoint=f"/cabinet/videos/{video_id}/player-url",
    )


@router.get("/videos/{video_id}/player-url", response_class=JSONResponse)
def refresh_catalog_player_url(
    video_id: int,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
):
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return _player_url_payload(video)


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
    return _save_progress(
        payload,
        user=user,
        db=db,
        bunny_video_id=video.bunny_video_id,
        known_duration_seconds=video.duration_seconds,
        topic_id=video.topic_id,
    )


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
        player_url_endpoint="/cabinet/video/player-url",
    )


@router.get("/video/player-url", response_class=JSONResponse)
def refresh_legacy_player_url(
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Пилотный ролик живёт только пока каталог пуст — те же условия, что у страницы."""
    if not settings.bunny_stream_enabled or db.query(LearningVideo.id).first():
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return _player_url_payload(legacy_pilot_video())


def _close_video_task_once(db: DBSession, *, user_id: int, topic_id: int) -> None:
    """Закрыть трекер-задачу недели по факту первого «досмотрел».

    Отдельная транзакция от `persist_video_progress`: та уже закоммитила
    прогресс, и сбой здесь не должен откатывать уже сохранённую позицию
    просмотра — в худшем случае трекер останется «в работе» до ручной отметки.
    """
    task = (
        db.query(TrackerTask)
        .filter(
            TrackerTask.topic_id == topic_id,
            TrackerTask.kind == ITEM_VIDEO,
            TrackerTask.deleted_at.is_(None),
            TrackerTask.is_published.is_(True),
        )
        .first()
    )
    if task is None:
        return
    try:
        close_task_for_user(db, task, user_id, source="auto")
        db.commit()
    except SQLAlchemyError:
        logger.exception("Tracker auto-close failed for user_id=%s, task_id=%s", user_id, task.id)
        db.rollback()


def _save_progress(
    payload: VideoProgressUpdate,
    *,
    user: dict,
    db: DBSession,
    bunny_video_id: str,
    known_duration_seconds: float | None = None,
    allow_client_duration: bool = False,
    topic_id: int | None = None,
):
    """Сохранить позицию просмотра. Факт «досмотрел» решает сервер.

    Длительность берётся только своя — её пишет `refresh_video_status` из поля
    `length` Bunny. Клиентской верить нельзя: валидатор ловит лишь позицию
    больше длительности, поэтому тело `{"position_seconds": 0,
    "duration_seconds": 1}` отмечало урок пройденным без секунды просмотра.

    Если своей длительности нет — отметку не ставим вовсе (fail-closed).
    Случай не гипотетический: миграция каталога вставляет опубликованный
    пилотный ролик без `duration_seconds`, а `publish_video` длительность не
    требует. Позиция просмотра при этом сохраняется как обычно, теряется только
    бейдж «просмотрено» — это дешевле, чем засчитанный без просмотра урок.

    `allow_client_duration` включён лишь для легаси-маршрута пилотного ролика: у
    него записи в каталоге нет в принципе, и сравнивать не с чем.

    `topic_id` — только у каталожных роликов (легаси пилотный ролик его не
    передаёт, autoclose для него не срабатывает, это осознанно). Момент «стало
    done впервые» ловится чтением состояния до апсерта: `persist_video_progress`
    делает атомарный `INSERT … ON CONFLICT`, из его возврата «стало ли только
    что true» не восстановить.
    """
    if known_duration_seconds is not None and known_duration_seconds > 0:
        duration = known_duration_seconds
    elif allow_client_duration:
        duration = payload.duration_seconds
    else:
        duration = None
    completed = (
        duration is not None
        and duration - payload.position_seconds <= 5
    )
    was_completed = False
    if completed and topic_id is not None:
        existing = get_video_progress(db, user_id=user["user_id"], video_id=bunny_video_id)
        was_completed = existing is not None and existing.completed_at is not None
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

    if completed and not was_completed and topic_id is not None:
        _close_video_task_once(db, user_id=user["user_id"], topic_id=topic_id)

    return JSONResponse({"ok": True, "completed": completed})


@router.post("/video/progress", response_class=JSONResponse)
def save_video_progress_legacy(
    payload: VideoProgressUpdate,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Прогресс пилотного ролика — те же условия, что у страницы и у player-url.

    Без проверки каталога маршрут продолжал писать прогресс на пилотный ролик и
    после того, как страница с ним перестала существовать: доступа это не давало,
    но три эндпоинта одной пары жили по разным правилам и разъехались бы дальше.
    """
    if not settings.bunny_stream_enabled or db.query(LearningVideo.id).first():
        return JSONResponse({"ok": False, "error": "video_disabled"}, status_code=404)
    return _save_progress(
        payload,
        user=user,
        db=db,
        bunny_video_id=settings.bunny_stream_video_id,
        allow_client_duration=True,
    )
