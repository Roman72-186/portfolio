from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.db.database import get_db
from app.dependencies import require_admin_role
from app.services.cases import CASE_GROWTH_THRESHOLD, build_case_rows
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


def _parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/cases", response_class=HTMLResponse)
def admin_cases_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    start_date: str = Query(""),
    end_date: str = Query(""),
    subject: str = Query(""),
):
    subject_clean = subject.strip()
    if subject_clean and subject_clean not in MOCK_SUBJECTS:
        subject_clean = ""

    start = _parse_date(start_date)
    end = _parse_date(end_date)
    page_error = None
    if (start_date.strip() and start is None) or (end_date.strip() and end is None):
        page_error = "Проверьте даты фильтра: нужен формат ГГГГ-ММ-ДД."
        rows = []
    elif start is not None and end is not None and start > end:
        page_error = "Дата начала не может быть позже даты окончания."
        rows = []
    else:
        rows = build_case_rows(
            db,
            start_date=start,
            end_date=end,
            subject=subject_clean,
        )

    return templates.TemplateResponse(
        "cabinet_cases.html",
        {
            "request": request,
            "user": user,
            "cases": rows,
            "total_cases": len(rows),
            "start_date": start_date.strip(),
            "end_date": end_date.strip(),
            "subject": subject_clean,
            "mock_subjects": MOCK_SUBJECTS,
            "threshold": int(CASE_GROWTH_THRESHOLD),
            "page_error": page_error,
        },
    )
