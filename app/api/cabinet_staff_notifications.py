"""Уведомления персонала (12.09.2026) — зеркало /cabinet/notifications у
ученика, но для куратора и выше. Первым источником были напоминания о дне
рождения ученика (exam_scheduler._run_birthday_check; с 29.09.2026 они
уходят ГП, а не куратору), но Notification.user_id ни на что не завязан,
так что сюда попадает любое уведомление, адресованное сотруднику.

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
    shown_unread_ids = [n.id for n in notifications if not n.is_read]
    unread_count = len(shown_unread_ids)
    if unread_count:
        # Только показанные: экран выводит 100 строк, а отметка всех разом
        # глотала хвост, который сотрудник так и не увидел (код-ревью
        # 28.09.2026, P3). Непрочитанные идут первыми — хвост доедет следом.
        db.query(Notification).filter(
            Notification.id.in_(shown_unread_ids),
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
