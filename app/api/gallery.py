import logging
from typing import Annotated

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_student
from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.utils import group_works
from app.tmpl import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cabinet")

PAGE_SIZE = 12


@router.get("/gallery", response_class=HTMLResponse)
async def cabinet_gallery(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    works = (
        db.query(Work)
        .filter(
            Work.user_id == user["user_id"],
            Work.status == "success",
        )
        .order_by(Work.year, Work.month, Work.created_at)
        .limit(1000)
        .all()
    )

    before_works = [w for w in works if w.work_type == WORK_TYPE_BEFORE]
    after_works = [w for w in works if w.work_type == WORK_TYPE_AFTER]
    mock_works = [w for w in works if w.work_type == WORK_TYPE_MOCK_EXAM]
    retake_works = [w for w in works if w.work_type == WORK_TYPE_RETAKE]

    drive_thumbs: dict[str, str] = {}
    needs_drive = any(w.drive_file_id and not w.s3_url for w in works)
    if needs_drive and user.get("tg_username"):
        try:
            from app.services.drive import list_student_photos
            photos = await list_student_photos(
                vk_id=user["vk_id"],
                tg_username=user["tg_username"],
            )
            drive_thumbs = {
                p["id"]: p["thumbnail_url"]
                for p in photos
                if p.get("id") and p.get("thumbnail_url")
            }
        except Exception as exc:
            logger.warning("gallery: drive fetch failed for vk_id=%s: %s", user["vk_id"], exc)

    return templates.TemplateResponse("gallery.html", {
        "request": request,
        "user": user,
        "before_groups": group_works(before_works),
        "after_groups": group_works(after_works),
        "mock_groups": group_works(mock_works),
        "retake_groups": group_works(retake_works),
        "before_count": len(before_works),
        "after_count": len(after_works),
        "mock_count": len(mock_works),
        "retake_count": len(retake_works),
        "total": len(works),
        "drive_thumbs": drive_thumbs,
        "page_size": PAGE_SIZE,
    })


@router.get("/gallery/thumb/{file_id}")
def gallery_thumb(
    file_id: str,
    user: Annotated[dict, Depends(require_student)],
):
    from app.services.drive import get_photo_thumbnail_url
    thumbnail_url = get_photo_thumbnail_url(file_id)
    if not thumbnail_url:
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    return RedirectResponse(url=thumbnail_url, status_code=302)


@router.get("/history", response_class=HTMLResponse)
def cabinet_history(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    from app.models.upload_log import UploadLog
    logs = (
        db.query(UploadLog)
        .filter(UploadLog.user_id == user["user_id"])
        .order_by(UploadLog.uploaded_at.desc())
        .limit(500)
        .all()
    )
    total_success = sum(1 for l in logs if l.status == "success")
    total_photos = sum(l.photo_count for l in logs if l.status == "success")
    return templates.TemplateResponse("history.html", {
        "request": request,
        "user": user,
        "logs": logs,
        "total_success": total_success,
        "total_failed": len(logs) - total_success,
        "total_photos": total_photos,
    })
