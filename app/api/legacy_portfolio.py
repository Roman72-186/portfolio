"""Read-only просмотр архивных фото (импорт из Telegram-чат-бота), по месяцам.

Отдельно от Work/циклов пробников намеренно — это исторический бэкфилл без
locks/notifications/feedback, см. app/models/legacy_portfolio_photo.py.
"""
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_students_shared import _require_student_panel, _check_access
from app.constants import MONTH_TO_NUM
from app.db.database import get_db
from app.models.legacy_portfolio_photo import LegacyPortfolioPhoto
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


def _group_by_month(photos: list[LegacyPortfolioPhoto]) -> list[dict]:
    groups: dict[tuple, list] = defaultdict(list)
    for p in photos:
        groups[(p.year, p.month)].append(p)

    result = []
    for (year, month), items in sorted(
        groups.items(),
        key=lambda kv: (kv[0][0], MONTH_TO_NUM.get(kv[0][1], 99)),
        reverse=True,
    ):
        result.append({
            "year": year,
            "month": month,
            "photos": sorted(items, key=lambda p: p.sent_at, reverse=True),
            "total": len(items),
        })
    return result


@router.get("/students/{student_id}/legacy-portfolio", response_class=HTMLResponse)
def legacy_portfolio_view(
    request: Request,
    student_id: int,
    user: Annotated[dict, Depends(_require_student_panel)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _check_access(student_id, user, db)

    photos = (
        db.query(LegacyPortfolioPhoto)
        .filter(LegacyPortfolioPhoto.user_id == student_id)
        .order_by(LegacyPortfolioPhoto.sent_at.desc())
        .limit(2000)
        .all()
    )

    return templates.TemplateResponse("legacy_portfolio.html", {
        "request": request,
        "user": user,
        "student": student,
        "groups": _group_by_month(photos),
        "total": len(photos),
    })
