"""Protected learning-video catalogue, playback and progress routes."""

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2.utils import htmlsafe_json_dumps
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db.database import get_db
from app.constants import VIDEO_WATCH_TAIL_SECONDS
from app.dependencies import (
    require_admin_role,
    require_csrf_header,
    require_learning_content_access,
    require_superadmin,
)
from app.models.learning_video import LearningVideo
from app.models.tracker import ITEM_VIDEO, TrackerTask
from app.services.bunny_stream import (
    BunnyStreamConfigError,
    build_signed_embed_url,
    player_js_url,
)
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
    evaluate_watch,
    save_video_progress as persist_video_progress,
    view_state,
    watch_shortfall_seconds,
    watch_threshold_seconds,
)
from app.tmpl import templates


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cabinet")


class VideoProgressUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_seconds: float = Field(ge=0, le=604_800, allow_inf_nan=False)
    duration_seconds: float | None = Field(default=None, gt=0, le=604_800, allow_inf_nan=False)
    playback_active: bool = False
    ended: bool = False

    @model_validator(mode="after")
    def validate_position(self):
        if self.duration_seconds is not None and self.position_seconds > self.duration_seconds + 5:
            raise ValueError("position_seconds cannot exceed duration_seconds")
        return self


def _not_found(request: Request, user: dict):
    return templates.TemplateResponse(request, "404.html", {"request": request, "user": user}, status_code=404)


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


# Адрес зеркала Bunny. Дублирует BRIDGE_TEST_DEFAULT_BASE ниже намеренно: тот
# объявлен рядом со страницей проверки, а этот нужен обычным маршрутам плеера
# выше по файлу.
BRIDGE_BASE = "https://video.assaru.space"
# Копия моста в России (Selectel, на одном сервере с apparchi.ru). `?bridge=ru`
# уводит через неё одного зрителя (владелец 06.10.2026): у ученицы домашний
# провайдер 10 минут не пускал к нидерландскому мосту, а сам сайт открывался.
BRIDGE_RU_BASE = "https://video-ru.assaru.space"


def _personal_bridge(user: dict, bridge: str | None) -> str | None:
    """`?bridge=1` уводит через зеркало одного зрителя — того, кто его дописал.

    Зачем (21.09.2026): мост, включённый всем сразу, на странице урока у
    владельца не запустил видео, хотя на служебной странице проверки то же
    видео через мост играло 20 секунд. Разница в боевой цепочке запуска —
    обложка, перезагрузка iframe с `autoplay`, сохранение прогресса, — а
    воспроизвести её можно только на самой странице урока. Повторять её на
    служебной странице бессмысленно: расхождение и будет причиной.

    Ограничения по роли здесь нет, и это осознанно (21.09.2026). Сначала флаг
    работал только со ранга >= 4, и владелец дважды не смог проверить мост:
    смотрит он из кабинета ученика, роль в сессии ученическая, флаг молча
    игнорировался — страница отдавала прямой адрес Bunny, провайдер его резал, и
    экран вечно висел на «Загружаем видео». Прятать тут нечего: адрес зеркала
    задан константой в коде, из запроса не берётся, а само зеркало отдаёт то же
    видео с той же подписью, что и Bunny. Заодно флагом можно выдать ссылку
    ученику, у которого видео не открывается, ещё до включения зеркала всем.

    Возвращает `None`, если флага нет — тогда `build_signed_embed_url` берёт
    глобальную настройку, то есть остальные ходят как ходили.
    """
    flag = str(bridge or "").strip().lower()
    if flag == "ru":
        return BRIDGE_RU_BASE
    # "nl" — значение переключателя моста у видео-блока (`TaskBlock.video_bridge`).
    if flag not in ("1", "true", "on", "nl"):
        return None
    return BRIDGE_BASE


def _bridge_query(proxy_base: str | None) -> str:
    """Флаг моста для адреса перевыпуска ссылки: без него через пять минут
    плеер тихо уехал бы на общую настройку и проверка смешала бы два моста."""
    if not proxy_base:
        return ""
    return "?bridge=ru" if proxy_base == BRIDGE_RU_BASE else "?bridge=1"


def _player_url_payload(video, *, proxy_base: str | None = None) -> JSONResponse:
    """Свежая подписанная ссылка для уже открытой страницы.

    Токен Bunny живёт минуты, а страница живёт часами: при любом перезапросе
    iframe (возврат на вкладку, перезапуск PWA, старт просмотра позже TTL)
    старый URL отдаёт заглушку. Поднимать TTL нельзя — страница плеера
    открывается и без Referer, то есть утёкшая ссылка играет откуда угодно.
    """
    try:
        player_url = build_signed_embed_url(
            video.bunny_video_id,
            library_id=getattr(video, "bunny_library_id", None),
            proxy_base=proxy_base,
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


def _player_payload(
    user: dict,
    db: DBSession,
    *,
    video,
    progress_endpoint: str,
    player_url_endpoint: str,
    proxy_base: str | None = None,
) -> tuple[dict, bool]:
    """Данные плеера — общие для страницы `/cabinet/videos/{id}` и инлайн-эндпоинта
    АОП (`/cabinet/videos/{id}/embed`). Второй элемент — признак ошибки конфигурации
    Bunny (тот же путь, что раньше возвращал 503 сразу из `_render_player`)."""
    viewer_name = " ".join(
        str(user.get(field) or "").strip()
        for field in ("first_name", "last_name")
    ).strip()
    if not viewer_name:
        viewer_name = str(user.get("name") or "").strip() or "Имя не указано"

    viewer_username = str(user.get("tg_username") or "").strip().lstrip("@")
    payload = {
        "video_title": video.title,
        "video_description": getattr(video, "description", None),
        "cover_url": getattr(video, "cover_s3_url", None),
        "progress_endpoint": progress_endpoint,
        "player_url_endpoint": player_url_endpoint,
        "player_url_ttl_seconds": settings.bunny_stream_token_ttl_seconds,
        # Адрес Player.js — через мост, когда он включён (см. `player_js_url`).
        "player_js_url": player_js_url(proxy_base),
        "viewer_watermark": {
            "name": viewer_name,
            "username": f"@{viewer_username}" if viewer_username else "Username не указан",
        },
    }
    try:
        payload["player_url"] = build_signed_embed_url(
            video.bunny_video_id,
            library_id=getattr(video, "bunny_library_id", None),
            proxy_base=proxy_base,
        )
    except BunnyStreamConfigError as exc:
        logger.error("Bunny Stream playback configuration error: %s", exc)
        payload["player_url"] = None
        return payload, True

    try:
        progress = get_video_progress(db, user_id=user["user_id"], video_id=video.bunny_video_id)
        payload["resume_position_seconds"] = get_resume_position(progress)
        # Позиция сдвинута назад ради зачёта — плеер объясняет ученику, сколько
        # досмотреть (05.10.2026).
        payload["resume_to_finish"] = watch_shortfall_seconds(progress) > 0
        payload["video_already_completed"] = bool(progress and progress.completed_at)
    except SQLAlchemyError:
        logger.exception("Video progress read failed for user_id=%s", user["user_id"])
        db.rollback()
        payload["resume_position_seconds"] = 0.0
        payload["resume_to_finish"] = False
        payload["video_already_completed"] = False

    return payload, False


# Ключи, которые реально читает `window.lrnVideoPlayer.mount()`
# (`_video_player.html`) — не весь `payload` из `_player_payload`, иначе
# `video_description` отрисовался бы дважды (в шапке страницы и в этом
# JSON), а мёртвый после удаления мини-опроса `video_already_completed`
# продолжил бы утекать в разметку без всякого потребителя.
_PLAYER_DATA_KEYS = (
    "player_url",
    "video_title",
    "cover_url",
    "viewer_watermark",
    "resume_position_seconds",
    "resume_to_finish",
    "progress_endpoint",
    "player_url_endpoint",
    "player_url_ttl_seconds",
    "player_js_url",
)


def _render_player(
    request: Request,
    user: dict,
    db: DBSession,
    *,
    video,
    progress_endpoint: str,
    player_url_endpoint: str,
    proxy_base: str | None = None,
    extra_context: dict | None = None,
):
    payload, has_error = _player_payload(
        user,
        db,
        video=video,
        progress_endpoint=progress_endpoint,
        player_url_endpoint=player_url_endpoint,
        proxy_base=proxy_base,
    )
    player_data = None
    if not has_error:
        # `|tojson` в Jinja экранирует не-ASCII в `\uXXXX` (ensure_ascii=True по
        # умолчанию) — имя и username зрителя ушли бы в разметку нечитаемой
        # escape-кашей. Собираем JSON вручную с ensure_ascii=False, но тем же
        # `htmlsafe_json_dumps`, что и `|tojson` внутри — экранирование
        # `<`/`>`/`&`/`'` (защита от разрыва `<script>`) остаётся на месте.
        player_data = htmlsafe_json_dumps(
            {key: payload[key] for key in _PLAYER_DATA_KEYS},
            dumps=json.dumps,
            ensure_ascii=False,
        )
    context = {
        "request": request,
        "user": user,
        "back_url": "/cabinet/admin/videos" if user.get("role_rank", 0) >= 4 else "/cabinet/videos",
        "player_data": player_data,
        **payload,
        **(extra_context or {}),
    }
    if has_error:
        return templates.TemplateResponse(request, "cabinet_video.html", context, status_code=503)
    return templates.TemplateResponse(request, "cabinet_video.html", context)


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
        items.append({
            "video": video,
            "resume_seconds": get_resume_position(progress),
            "state": view_state(progress),
        })
    return templates.TemplateResponse(request, "cabinet_videos.html",
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
    bridge: str | None = None,
):
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return _not_found(request, user)
    try:
        log_video_view(db, user_id=user["user_id"], video_id=video.bunny_video_id)
    except SQLAlchemyError:
        logger.exception("Video view log failed for user_id=%s", user["user_id"])
        db.rollback()
    # Личное зеркало держится и на перевыпуске ссылки: токен живёт минуты, и без
    # `?bridge=1` в этом адресе страница через пять минут тихо уехала бы на
    # прямой Bunny — а там у владельца без VPN видео не идёт.
    proxy_base = _personal_bridge(user, bridge)
    refresh_endpoint = f"/cabinet/videos/{video_id}/player-url" + _bridge_query(proxy_base)
    return _render_player(
        request,
        user,
        db,
        video=video,
        progress_endpoint=f"/cabinet/videos/{video_id}/progress",
        player_url_endpoint=refresh_endpoint,
        proxy_base=proxy_base,
    )


@router.get("/videos/{video_id}/embed", response_class=JSONResponse)
def cabinet_video_embed(
    video_id: int,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
    bridge: str | None = None,
):
    """JSON-вариант `_render_player` для инлайн-карточки на АОП (без перехода
    на `/cabinet/videos/{id}`). Та же проверка доступа (`require_learning_content_access`
    — заворачивает ученика вне группы 403-м с понятным сообщением), тот же
    `_video_for_viewer`, только без полного рендера страницы.

    `?bridge=` дописывает сервер, когда у видео-блока выбран свой мост
    (`TaskBlock.video_bridge`, владелец 06.10.2026); флаги те же, что у
    страницы урока, и так же только из закрытого списка адресов."""
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    try:
        log_video_view(db, user_id=user["user_id"], video_id=video.bunny_video_id)
    except SQLAlchemyError:
        logger.exception("Video view log failed for user_id=%s", user["user_id"])
        db.rollback()
    proxy_base = _personal_bridge(user, bridge)
    payload, has_error = _player_payload(
        user,
        db,
        video=video,
        progress_endpoint=f"/cabinet/videos/{video_id}/progress",
        player_url_endpoint=f"/cabinet/videos/{video_id}/player-url" + _bridge_query(proxy_base),
        proxy_base=proxy_base,
    )
    if has_error:
        return JSONResponse({"ok": False, "error": "player_unavailable"}, status_code=503)
    return JSONResponse({"ok": True, **payload})


@router.get("/videos/{video_id}/player-url", response_class=JSONResponse)
def refresh_catalog_player_url(
    video_id: int,
    user: Annotated[dict, Depends(require_learning_content_access)],
    db: Annotated[DBSession, Depends(get_db)],
    bridge: str | None = None,
):
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return _player_url_payload(video, proxy_base=_personal_bridge(user, bridge))


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


# Адреса моста, которые странице проверки разрешено открывать. Значение уходит
# в `src` скрипта и в `src` iframe, поэтому произвольную строку из `?bridge=`
# сюда не пускаем даже на странице для администратора.
BRIDGE_TEST_DEFAULT_BASE = "https://video.assaru.space"
# Копия моста на Selectel (владелец, 26.09.2026): тот же конфиг nginx, но в
# России, рядом с самим Apparchi. Заведена для сравнения с нидерландским мостом,
# ученики через неё не ходят.
BRIDGE_TEST_SELECTEL_BASE = BRIDGE_RU_BASE
BRIDGE_TEST_ALLOWED_BASES = (BRIDGE_TEST_DEFAULT_BASE, BRIDGE_TEST_SELECTEL_BASE)
# Подписи переключателя на странице: какой мост где стоит.
BRIDGE_TEST_CHOICES = (
    (BRIDGE_TEST_DEFAULT_BASE, "Нидерланды"),
    (BRIDGE_TEST_SELECTEL_BASE, "Selectel, Россия"),
)


@router.get("/admin/video-bridge-test", response_class=HTMLResponse)
def video_bridge_test(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    video_id: int | None = None,
    bridge: str | None = None,
):
    """Страница проверки моста до Bunny — только для владельца и админов.

    Зачем отдельная страница (владелец 20.09.2026): мост включали дважды на
    всех сразу, и оба раза ученики оставались без видео, причём в логе моста не
    было ни одного запроса с их адресов. Значит обрыв где-то между устройством
    ученика и `video.assaru.space`, а серверная сторона выглядит здоровой.
    Здесь мостовой и прямой плееры стоят рядом, а проверки связи печатают
    результат прямо на экране: владелец тестирует с телефона и iPad, консоли
    браузера там нет.

    Адрес моста берётся из этого маршрута, а не из `BUNNY_PLAYER_PROXY_BASE`.
    Глобальная переменная на проде остаётся пустой, ученики смотрят как
    раньше — напрямую.
    """
    bridge_base = (bridge or "").strip().rstrip("/") or BRIDGE_TEST_DEFAULT_BASE
    if bridge_base not in BRIDGE_TEST_ALLOWED_BASES:
        raise HTTPException(status_code=400, detail="Этот адрес моста не разрешён")

    videos = list_published_videos(db, viewer=user)
    video = None
    if video_id is not None:
        video = next((item for item in videos if getattr(item, "id", None) == video_id), None)
    elif videos:
        video = videos[0]

    bridge_player_url = None
    direct_player_url = None
    config_error = None
    if video is not None:
        try:
            library_id = getattr(video, "bunny_library_id", None)
            bridge_player_url = build_signed_embed_url(
                video.bunny_video_id, library_id=library_id, proxy_base=bridge_base
            )
            direct_player_url = build_signed_embed_url(
                video.bunny_video_id, library_id=library_id, proxy_base=""
            )
        except BunnyStreamConfigError as exc:
            logger.error("Bunny Stream playback configuration error: %s", exc)
            config_error = str(exc)

    return templates.TemplateResponse(
        request,
        "cabinet_video_bridge_test.html",
        {
            "request": request,
            "user": user,
            "back_url": "/cabinet/admin/videos",
            "video": video,
            "videos": videos,
            "bridge_base": bridge_base,
            "bridge_choices": BRIDGE_TEST_CHOICES,
            "bridge_player_url": bridge_player_url,
            "direct_player_url": direct_player_url,
            "bridge_player_js_url": player_js_url(bridge_base),
            "direct_player_js_url": player_js_url(""),
            "config_error": config_error,
            # Мост включён глобально — значит эта страница уже не отличается от
            # ученической, и об этом честнее сказать вслух.
            "bridge_enabled_globally": bool(settings.bunny_player_proxy_base),
            # Метка попадает в адрес каждой проверки, поэтому её видно в логе
            # моста: по ней прогон с устройства владельца отделяется от чужого
            # трафика.
            "probe_id": secrets.token_hex(3),
            "watch_tail_seconds": VIDEO_WATCH_TAIL_SECONDS,
            "can_watch_check": user.get("role_rank", 0) >= 5,
        },
    )


# ── Проверка контроля просмотра: только суперадмин (владелец 24.09.2026) ─────
#
# «Сделаем эту проверку только для меня, не для кого больше». Здесь владелец
# смотрит урок глазами ученика через мост и видит панель контроля просмотра.
# Правило зачёта сначала жило только здесь, а после проверки на 1× и 2× в тот же
# день перенесено на всех учеников (`_save_progress`) — поэтому прогресс
# страница пишет через тот же маршрут, что и ученик, в строку самого
# суперадмина.
# Путь маршрута — без `/cabinet`: префикс уже стоит у роутера.
WATCH_CHECK_ROUTE = "/admin/video-bridge-test"
WATCH_CHECK_BASE = "/cabinet" + WATCH_CHECK_ROUTE


def _watch_state(db: DBSession, *, user_id: int, video) -> dict:
    """Состояние контроля просмотра по сохранённой строке — те же формулы, что
    у `evaluate_watch`, чтобы панель не разошлась с решением."""
    progress = get_video_progress(db, user_id=user_id, video_id=video.bunny_video_id)
    duration = video.duration_seconds if video.duration_seconds and video.duration_seconds > 0 else None
    watched = progress.watched_seconds if progress else 0.0
    credited = watched
    if progress is not None and progress.completed_at is not None:
        credited = max(0.0, watched - progress.last_completion_watched_seconds)
    last_completed = (progress.last_completed_at or progress.completed_at) if progress else None
    return {
        "ok": True,
        "duration_seconds": duration,
        "threshold_seconds": watch_threshold_seconds(duration) if duration else None,
        "position_seconds": progress.position_seconds if progress else 0.0,
        "watched_seconds": watched,
        "credited_this_pass": credited,
        "completed": last_completed is not None,
    }


@router.get(WATCH_CHECK_ROUTE + "/player", response_class=HTMLResponse)
def video_watch_check_player(
    request: Request,
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    video_id: int,
    bridge: str | None = None,
):
    """Урок глазами ученика через мост — тот же `cabinet_video.html` с тем же
    плеером, водяным знаком и кнопками, плюс панель контроля просмотра.

    Мост — тот, что выбран переключателем страницы проверки (владелец
    06.10.2026: «проверить там же, но по всем параметрам»). До этого шаг 3
    всегда шёл через Нидерланды, и российскую копию целиком, с плеером,
    сохранением места и зачётом, проверить было нечем."""
    bridge_base = (bridge or "").strip().rstrip("/") or BRIDGE_TEST_DEFAULT_BASE
    if bridge_base not in BRIDGE_TEST_ALLOWED_BASES:
        raise HTTPException(status_code=400, detail="Этот адрес моста не разрешён")
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return _not_found(request, user)
    # Перевыпуск ссылки — через тот же мост, иначе через пять минут плеер
    # уехал бы на другой и проверка смешала бы два моста.
    refresh_flag = "ru" if bridge_base == BRIDGE_RU_BASE else "1"
    return _render_player(
        request,
        user,
        db,
        video=video,
        progress_endpoint=f"/cabinet/videos/{video_id}/progress",
        player_url_endpoint=f"/cabinet/videos/{video_id}/player-url?bridge={refresh_flag}",
        proxy_base=bridge_base,
        extra_context={
            "back_url": f"{WATCH_CHECK_BASE}?video_id={video_id}&bridge={quote(bridge_base, safe='')}",
            "watch_debug": {
                "state_endpoint": f"{WATCH_CHECK_BASE}/watch-state?video_id={video_id}",
                "reset_endpoint": f"{WATCH_CHECK_BASE}/reset?video_id={video_id}",
            },
        },
    )


@router.get(WATCH_CHECK_ROUTE + "/watch-state", response_class=JSONResponse)
def video_watch_check_state(
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    video_id: int,
):
    """Только своя строка: `user_id` из запроса не берём."""
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return JSONResponse(_watch_state(db, user_id=user["user_id"], video=video))


@router.post(WATCH_CHECK_ROUTE + "/reset", response_class=JSONResponse)
def video_watch_check_reset(
    user: Annotated[dict, Depends(require_superadmin)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
    video_id: int,
):
    """Стереть свой прогресс по ролику, чтобы проверить с нуля. Трогает только
    строку самого суперадмина."""
    video = _video_for_viewer(db, catalog_id=video_id, user=user)
    if video is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    progress = get_video_progress(db, user_id=user["user_id"], video_id=video.bunny_video_id)
    if progress is not None:
        db.delete(progress)
        db.commit()
    return JSONResponse(_watch_state(db, user_id=user["user_id"], video=video))


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
    if not task.is_required:
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

    Защита от перемотки (владелец 05.09.2026): позиция у конца ролика — не
    единственное условие. Перемотка ползунком выставляет `position_seconds`
    рядом с длительностью за одно движение, поэтому вдобавок требуем, чтобы
    набрались честно проигранные секунды ролика. Порог — за 30 секунд до
    конца, ускорение засчитывается (владелец 24.09.2026, после проверки на
    странице моста). Решение целиком — `evaluate_watch` в
    `app/services/video_progress.py`, его же показывает панель проверки.
    """
    if known_duration_seconds is not None and known_duration_seconds > 0:
        duration = known_duration_seconds
    elif allow_client_duration:
        duration = payload.duration_seconds
    else:
        duration = None

    existing = get_video_progress(db, user_id=user["user_id"], video_id=bunny_video_id)
    was_completed = existing is not None and existing.completed_at is not None
    decision = evaluate_watch(
        existing,
        position_seconds=payload.position_seconds,
        duration_seconds=duration,
        playback_active=payload.playback_active,
        ended=payload.ended,
    )
    watched_seconds = decision.watched_seconds
    completed = decision.completed
    if decision.skipped_seconds >= 1:
        # Каждый срезанный кусок — в лог (владелец 06.10.2026): по паузе между
        # отметками и флагу воспроизведения видно, перемотка это или сеть.
        # Без строки на жалобу «смотрела до конца» ответить было нечем.
        updated_at = existing.updated_at
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        gap = (datetime.now(timezone.utc) - updated_at).total_seconds() if updated_at else None
        logger.warning(
            "Видео: кусок не засчитан | user=%s | video=%s | позиция %.0f→%.0f"
            " | срезано=%.0f | пауза между отметками=%s | играло=%s | засчитано всего=%.0f",
            user["user_id"], bunny_video_id, existing.position_seconds,
            payload.position_seconds, decision.skipped_seconds,
            f"{gap:.0f}" if gap is not None else "?", payload.playback_active,
            watched_seconds,
        )
    try:
        completed = persist_video_progress(
            db,
            user_id=user["user_id"],
            video_id=bunny_video_id,
            position_seconds=payload.position_seconds,
            # Для каталожного ролика храним длительность Bunny, а не значение из
            # браузера. Иначе один запрос с другой длительностью ломал бы возобновление.
            duration_seconds=duration,
            completed=completed,
            watched_seconds=watched_seconds,
        )
    except SQLAlchemyError:
        logger.exception("Video progress save failed for user_id=%s", user["user_id"])
        db.rollback()
        return JSONResponse({"ok": False, "error": "save_failed"}, status_code=503)

    if completed and not was_completed and topic_id is not None:
        _close_video_task_once(db, user_id=user["user_id"], topic_id=topic_id)

    # `skipped` — ученик перемотал вперёд, перемотанное не засчитано: плеер
    # предупреждает сразу, а не после досмотра (владелец 06.10.2026).
    return JSONResponse({"ok": True, "completed": completed, "skipped": decision.skipped})


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
