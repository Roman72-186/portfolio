"""Read-only просмотр архивных фото (импорт из Telegram-чат-бота), по месяцам.

Отдельно от Work/циклов пробников намеренно — это исторический бэкфилл без
locks/notifications/feedback, см. app/models/legacy_portfolio_photo.py.

Для АРХИВНОГО ученика (владелец 21.09.2026: «помести все фото в одну вкладку -
Архив») эта же страница дополнительно показывает «До обучения» и «В процессе
обучения» (то же слияние Work+сдачи+финалы пробника, что и в
`student_portfolio_after_groups`) — вместо отдельных вкладок «Портфолио»/
«Пробники» на карточке. Для действующего ученика поведение не меняется:
кнопка «Архив» на карточке скрыта без исторических фото, страница показывает
только их.
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
from app.models.work import WORK_TYPE_BEFORE, Work
from app.services.portfolio import student_portfolio_after_groups
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


def _to_gallery_groups(groups: list[dict]) -> list[dict]:
    """Приводит `group_works`-группы (поле `works`, объекты с `.filename`) к
    тому же виду, что уже отдаёт `_group_by_month` для `LegacyPortfolioPhoto`
    (поле `photos`, словари с `original_filename`) — шаблон рисует оба одним
    макросом."""
    return [
        {
            "year": g["year"],
            "month": g["month"],
            "total": g["total"],
            "photos": [
                {"s3_url": item.s3_url, "original_filename": item.filename}
                for item in g["works"]
            ],
        }
        for g in groups
    ]


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
    # GET остаётся read-only, поэтому Главный преподаватель и суперадмин
    # могут открыть исторические фото архивного ученика из его карточки.
    student = _check_access(student_id, user, db, read_archive=True)

    photos = (
        db.query(LegacyPortfolioPhoto)
        .filter(LegacyPortfolioPhoto.user_id == student_id)
        .order_by(LegacyPortfolioPhoto.sent_at.desc())
        .limit(2000)
        .all()
    )

    is_archived_student = student.archived_at is not None
    before_photos: list[dict] = []
    after_groups: list[dict] = []
    if is_archived_student:
        before_works = (
            db.query(Work)
            .filter(Work.user_id == student_id, Work.work_type == WORK_TYPE_BEFORE, Work.status == "success")
            .order_by(Work.created_at.desc())
            .limit(300)
            .all()
        )
        before_photos = [{"s3_url": w.s3_url, "original_filename": w.filename} for w in before_works]
        after_groups = _to_gallery_groups(student_portfolio_after_groups(db, student_id))

    return templates.TemplateResponse(request, "legacy_portfolio.html", {
        "request": request,
        "user": user,
        "student": student,
        "is_archived_student": is_archived_student,
        "before_photos": before_photos,
        "after_groups": after_groups,
        "groups": _group_by_month(photos),
        "total": len(photos),
    })
