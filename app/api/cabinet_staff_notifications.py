"""Уведомления персонала (12.09.2026) — зеркало /cabinet/notifications у
ученика, но для куратора и выше. Первый и пока единственный источник —
напоминания о дне рождения ученика (exam_scheduler._run_birthday_check),
но Notification.user_id ни на что не завязан, так что сюда же попадёт любое
будущее уведомление, адресованное сотруднику.

require_curator (rank >= 2) — не только куратор: если когда-нибудь появится
уведомление для админа/суперадмина, роут уже готов, менять не придётся.
Пункт меню при этом видит только куратор — см. _curator_nav.html.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.db.database import get_db
from app.dependencies import require_curator
from app.models.notification import Notification
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


@router.get("/staff/notifications", response_class=HTMLResponse)
def staff_notifications(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
):
    notifications = (
        db.query(Notification)
        .filter(Notification.user_id == user["user_id"])
        .order_by(Notification.is_read.asc(), Notification.created_at.desc())
        .limit(100)
        .all()
    )
    unread_count = sum(1 for n in notifications if not n.is_read)
    if unread_count:
        db.query(Notification).filter(
            Notification.user_id == user["user_id"],
            Notification.is_read.is_(False),
        ).update({"is_read": True, "read_at": datetime.now(timezone.utc)})
        db.commit()
        invalidate_unread(user["user_id"])

    return templates.TemplateResponse(request, "cabinet_staff_notifications.html", {
        "request": request,
        "user": user,
        "notifications": notifications,
        "unread_count": unread_count,
        "nav_active": "notifications",
    })
