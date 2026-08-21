"""Учебные программы: календарь и сборка дня.

Экран Главного преподавателя (ранг роли >= 4). Месяц листается ссылкой
`?month=YYYY-MM`, сетка и отметки считаются на сервере: JS здесь только
раскрывает панели и шлёт сохранение.

Файл новый намеренно: `cabinet_admin.py` ведёт параллельная сессия, а
`cabinet_tracker_admin.py` уже большой и отвечает за разовые задачи.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header
from app.models.tracker import ITEM_KIND_LABELS
from app.services.program import (
    WEEKDAY_LABELS,
    item_details,
    items_for_day,
    month_days,
    month_marks,
    shift_month,
    tags_split,
)
from app.services.tracker import delete_task, get_task
from app.services.tz import today_msk
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/program")

MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def _parse_month(raw: str | None, today: date) -> tuple[int, int]:
    """`?month=2026-09` → (2026, 9). Мусор и пустое значение — текущий месяц."""
    if not raw:
        return today.year, today.month
    try:
        year, month = raw.split("-", 1)
        year_num, month_num = int(year), int(month)
    except (ValueError, AttributeError):
        return today.year, today.month
    if not 1 <= month_num <= 12 or not 2000 <= year_num <= 2100:
        return today.year, today.month
    return year_num, month_num


def _parse_day(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail="Такого дня нет")


@router.get("", response_class=HTMLResponse)
def program_month(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    month: str | None = None,
):
    today = today_msk()
    year, month_num = _parse_month(month, today)
    prev_year, prev_month = shift_month(year, month_num, -1)
    next_year, next_month = shift_month(year, month_num, 1)
    return templates.TemplateResponse(
        "cabinet_program.html",
        {
            "request": request,
            "user": user,
            "days": month_days(year, month_num, today),
            "marks": month_marks(db, year, month_num),
            "weekday_labels": WEEKDAY_LABELS,
            "kind_labels": ITEM_KIND_LABELS,
            "month_title": f"{MONTH_NAMES[month_num - 1].capitalize()} {year}",
            "prev_month": f"{prev_year}-{prev_month:02d}",
            "next_month": f"{next_year}-{next_month:02d}",
            "current_month": f"{today.year}-{today.month:02d}",
            "today_iso": today.isoformat(),
        },
    )


@router.get("/{iso}", response_class=HTMLResponse)
def program_day(
    iso: str,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    day = _parse_day(iso)
    today = today_msk()
    items = items_for_day(db, day)
    tariff_tags, other_tags = tags_split(db)
    return templates.TemplateResponse(
        "cabinet_program_day.html",
        {
            "request": request,
            "user": user,
            "day": day,
            "day_iso": day.isoformat(),
            "day_title": (
                f"{day.day} {MONTH_NAMES[day.month - 1]} {day.year}, "
                f"{_weekday_full(day)}"
            ),
            # Прошлое только смотрим: элемент задним числом открылся бы ученикам
            # мгновенно, и «поставить на вчера» почти всегда опечатка.
            "is_past": day < today,
            "month_href": f"/cabinet/staff/program?month={day.year}-{day.month:02d}",
            "items": items,
            "details": item_details(db, items),
            "kind_labels": ITEM_KIND_LABELS,
            "subjects": MOCK_SUBJECTS,
            "tariff_tags": tariff_tags,
            "other_tags": other_tags,
        },
    )


@router.post("/items/{task_id}/delete", response_class=JSONResponse)
def delete_program_item(
    task_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Элемент не найден")
    delete_task(task)
    db.commit()
    return JSONResponse({"ok": True})


WEEKDAY_FULL = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)


def _weekday_full(day: date) -> str:
    return WEEKDAY_FULL[day.weekday()]
