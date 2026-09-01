from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import mimetypes
import os

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from slowapi.errors import RateLimitExceeded

from app.db.database import engine, Base, SessionLocal
from app.config import settings
from app.api import auth, cabinet, upload, gallery
from app.api import cabinet_student, cabinet_curator, cabinet_admin, cabinet_superadmin
from app.api import cabinet_students_shared, cabinet_tags, cases
from app.api import cycle_upload, feedback as feedback_router
from app.api import legacy_portfolio, video, video_admin
from app.api import guest_exam, cabinet_guest_exam_admin
from app.api import cabinet_learning, cabinet_personal, cabinet_tracker_admin, cabinet_program
from app.api import cabinet_digest_admin, cabinet_goal_admin
from app.api import cabinet_tracker
from app.api import homework_submission
from app.api import lab_assets
from app.api import task_block_review
from app.api import student_review
from app.limiter import limiter
from app.services.rbac import seed_roles_and_permissions
from app.services import n8n as n8n_service
from app.services import vk as vk_service
from app.services import telegram as telegram_service
from app.services import telegram_login as telegram_login_service
from app.services import drive as drive_service
from app.services import exam_scheduler
import app.models  # noqa: F401 — ensures all models are registered with Base.metadata
from app.models.session import Session as DbSession


_FORCE_SESSION_REFRESH_PATHS = {
    "/upload/mock-exam",
    "/upload/mock-exam/csrf",
}


def _should_force_session_refresh(request: Request) -> bool:
    return request.method == "GET" and request.url.path in _FORCE_SESSION_REFRESH_PATHS


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import settings
    if settings.session_secret == "change-me":
        raise RuntimeError("SESSION_SECRET не задан в .env — запуск в продакшене с дефолтным секретом запрещён")
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_roles_and_permissions(db)
    finally:
        db.close()
    if settings.n8n_enabled:
        await n8n_service.init_client()
        await drive_service.init_client()
    await vk_service.init_client()
    await telegram_service.init_client()
    await telegram_login_service.init_client()
    should_start_scheduler = (
        not os.environ.get("PYTEST_CURRENT_TEST")
        and settings.database_url != "sqlite:///:memory:"
    )
    if should_start_scheduler:
        exam_scheduler.start_scheduler()
    yield
    if settings.n8n_enabled:
        await n8n_service.close_client()
        await drive_service.close_client()
    await vk_service.close_client()
    await telegram_service.close_client()
    await telegram_login_service.close_client()
    if should_start_scheduler:
        exam_scheduler.stop_scheduler()


_docs_enabled = settings.env != "production"
app = FastAPI(
    title="Путь к сотке!",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Rate limiting
app.state.limiter = limiter

# Compress responses (large static JS bundles like vendored three.js benefit most)
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Слишком много запросов. Подождите минуту."},
    )


# Ассеты 3D-лаборатории просит не человек, а fetch и <video>. Обработчики ниже
# уводят браузер на страницу входа или рисуют HTML-заглушку — для навигации это
# правильно, а для запроса модели означает 200 с версткой вместо файла: загрузчик
# glTF давится ею, и в консоли вместо внятного «нет доступа» появляется мусор.
# Поэтому на этом префиксе отдаём голый код ответа.
def _is_lab_asset(request: Request) -> bool:
    return request.url.path.startswith("/lab/asset/")


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    if _is_lab_asset(request):
        return JSONResponse(status_code=403, content={"detail": getattr(exc, "detail", "Forbidden")})
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    if "application/json" in accept or "application/json" in content_type:
        detail = getattr(exc, "detail", "Forbidden")
        return JSONResponse(status_code=403, content={"detail": detail})
    from app.tmpl import templates
    detail = getattr(exc, "detail", "")
    if "заблокирован" in detail.lower():
        reason = "Ваш аккаунт заблокирован. Обратитесь к администратору."
    elif "удалён" in detail.lower():
        reason = "Аккаунт был удалён."
    else:
        reason = detail or "Доступ запрещён"
    return templates.TemplateResponse("blocked.html", {"request": request, "reason": reason}, status_code=403)


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    if _is_lab_asset(request):
        return JSONResponse(status_code=401, content={"detail": getattr(exc, "detail", "Unauthorized")})
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    if "application/json" in accept or "application/json" in content_type:
        detail = getattr(exc, "detail", "Unauthorized")
        return JSONResponse(status_code=401, content={"detail": detail})
    return RedirectResponse("/?error=session_expired", status_code=302)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if _is_lab_asset(request):
        return JSONResponse(status_code=404, content={"detail": getattr(exc, "detail", "Not found")})
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    from app.tmpl import templates
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    from app.tmpl import templates
    return templates.TemplateResponse("404.html", {"request": request}, status_code=500)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc):
    if isinstance(exc, HTTPException):
        raise exc
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    from app.tmpl import templates
    return templates.TemplateResponse("404.html", {"request": request}, status_code=500)


# Static files
mimetypes.add_type("image/webp", ".webp")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# Service Worker для Web Push (Фаза 3) — отдельный роут вне /static/, чтобы
# заголовок Service-Worker-Allowed: / дал воркеру область действия на весь
# origin, а не только /static/*.
@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        "app/static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )

# Security headers middleware
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# Cache-control middleware for HTML responses.
# `private, max-age=0, must-revalidate` заставляет браузер ревалидировать страницу
# при навигации, но НЕ блокирует bfcache (back-forward cache) — это даёт мгновенный
# back/forward без перезагрузки. `no-store` ломает bfcache, поэтому мы его не ставим.
# Static assets (/static/) получают долгосрочный кэш — при обновлении меняй ?v= в URL.
#
# Исключение: /static/3dlab/js/ — несобираемый (без хэшей в именах файлов),
# часто меняющийся исходный код 3D-лаборатории. immutable-кэш на год означал, что
# правки логики (не самих 3D-моделей — тех отдельный IndexedDB-кэш в cachedFetch.js)
# не доходили до уже открывавших /3dlab пользователей без ручного докручивания ?v=
# по всей цепочке ES-импортов — процесс уже расходился (models.js грузится по двум
# разным URL). Тут — no-cache + ревалидация по ETag/Last-Modified (StaticFiles отдаёт
# их из коробки), браузер получает дешёвый 304 при отсутствии изменений.
@app.middleware("http")
async def cache_control(request: Request, call_next):
    response: Response = await call_next(request)
    # Видео — единственное место, где `no-store` перевешивает bfcache: страница
    # несёт подписанный embed-URL и ФИО с телефоном зрителя в ватермарке, и
    # оставлять её копию в дисковом кэше нельзя. Плата за это — bfcache здесь
    # ненадёжен: часть браузеров такую страницу не восстанавливает, а
    # перезагружает. Обработчик `pageshow`/`persisted` в cabinet_video.html —
    # страховка для тех, кто всё же восстановит, а не рабочий путь по умолчанию.
    if request.url.path.startswith("/cabinet/video") or request.url.path.startswith("/cabinet/admin/videos"):
        response.headers["Cache-Control"] = "private, no-store"
        return response
    if request.url.path.startswith("/static/3dlab/js/"):
        response.headers["Cache-Control"] = "no-cache"
        return response
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return response


@app.middleware("http")
async def force_mock_exam_session_refresh(request: Request, call_next):
    response: Response = await call_next(request)
    if not _should_force_session_refresh(request):
        return response

    session_id = request.cookies.get("session_id")
    if not session_id:
        return response

    db = SessionLocal()
    try:
        session = (
            db.query(DbSession)
            .filter(DbSession.id == session_id, DbSession.is_active == True)  # noqa: E712
            .first()
        )
        if session is None:
            return response
        now = datetime.now(timezone.utc)
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            return response

        ttl = timedelta(hours=settings.session_ttl_hours)
        session.expires_at = now + ttl
        db.commit()
        response.set_cookie(
            key="session_id",
            value=session.id,
            httponly=True,
            samesite="lax",
            max_age=settings.session_ttl_hours * 3600,
            secure=True,
            path="/",
        )
    finally:
        db.close()
    return response


# Routers
app.include_router(auth.router)
app.include_router(cabinet.router)
app.include_router(cabinet_student.router)
app.include_router(cabinet_curator.router)
app.include_router(cabinet_admin.router)
app.include_router(cabinet_superadmin.router)
app.include_router(cabinet_tags.router)
app.include_router(cases.router)
app.include_router(cabinet_students_shared.router)
app.include_router(upload.router)
app.include_router(cycle_upload.router)
app.include_router(feedback_router.router)
app.include_router(gallery.router)
app.include_router(legacy_portfolio.router)
app.include_router(video.router)
app.include_router(video_admin.router)
app.include_router(guest_exam.router)
app.include_router(cabinet_guest_exam_admin.router)
app.include_router(cabinet_learning.router)
app.include_router(cabinet_personal.router)
app.include_router(cabinet_tracker_admin.router)
app.include_router(cabinet_digest_admin.router)
app.include_router(cabinet_goal_admin.router)
app.include_router(cabinet_program.router)
app.include_router(cabinet_tracker.router)
app.include_router(homework_submission.router)
app.include_router(lab_assets.router)
app.include_router(task_block_review.router)
app.include_router(student_review.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/404", response_class=HTMLResponse)
async def page_404(request: Request):
    from app.tmpl import templates
    return templates.TemplateResponse("404.html", {"request": request}, status_code=200)
