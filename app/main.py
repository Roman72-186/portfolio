from contextlib import asynccontextmanager
import mimetypes
import os

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.db.database import engine, Base, SessionLocal
from app.api import auth, cabinet, upload, admin, gallery
from app.api import cabinet_student, cabinet_curator, cabinet_admin, cabinet_superadmin
from app.api import cabinet_students_shared
from app.api import cycle_upload, feedback as feedback_router
from app.limiter import limiter
from app.services.rbac import seed_roles_and_permissions
from app.services import n8n as n8n_service
from app.services import vk as vk_service
from app.services import exam_scheduler
import app.models  # noqa: F401 — ensures all models are registered with Base.metadata


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
    await n8n_service.init_client()
    await vk_service.init_client()
    should_start_scheduler = (
        not os.environ.get("PYTEST_CURRENT_TEST")
        and settings.database_url != "sqlite:///:memory:"
    )
    if should_start_scheduler:
        exam_scheduler.start_scheduler()
    yield
    await n8n_service.close_client()
    await vk_service.close_client()
    if should_start_scheduler:
        exam_scheduler.stop_scheduler()


app = FastAPI(title="Портфолио", lifespan=lifespan)

# Rate limiting
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Слишком много запросов. Подождите минуту."},
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
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
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    if "application/json" in accept or "application/json" in content_type:
        detail = getattr(exc, "detail", "Unauthorized")
        return JSONResponse(status_code=401, content={"detail": detail})
    return RedirectResponse("/?error=session_expired", status_code=302)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
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
@app.middleware("http")
async def cache_control(request: Request, call_next):
    response: Response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return response


# Routers
app.include_router(auth.router)
app.include_router(cabinet.router)
app.include_router(cabinet_student.router)
app.include_router(cabinet_curator.router)
app.include_router(cabinet_admin.router)
app.include_router(cabinet_superadmin.router)
app.include_router(cabinet_students_shared.router)
app.include_router(upload.router)
app.include_router(cycle_upload.router)
app.include_router(feedback_router.router)
app.include_router(gallery.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/404", response_class=HTMLResponse)
async def page_404(request: Request):
    from app.tmpl import templates
    return templates.TemplateResponse("404.html", {"request": request}, status_code=200)
