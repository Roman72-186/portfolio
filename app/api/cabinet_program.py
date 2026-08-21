"""Учебные программы: календарь и сборка дня.

Экран Главного преподавателя (ранг роли >= 4). Месяц листается ссылкой
`?month=YYYY-MM`, сетка и отметки считаются на сервере: JS здесь только
раскрывает панели и шлёт сохранение.

Файл новый намеренно: `cabinet_admin.py` ведёт параллельная сессия, а
`cabinet_tracker_admin.py` уже большой и отвечает за разовые задачи.
"""

import json
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header
from app.models.audit_log import AuditLog
from app.models.exam_assignment import ExamAssignment
from app.models.tracker import ITEM_KIND_LABELS, ITEM_MOCK_EXAM, SOURCE_EXAM_ASSIGNMENT
from app.services.exam_tickets import (
    compose_assignment_title,
    create_ticket,
    default_schedule_for_day,
    ensure_mock_period_for,
    next_seq_number,
    parse_msk_datetime,
    validate_tags,
    validate_window,
)
from app.services.program import (
    WEEKDAY_LABELS,
    ensure_item_topic,
    item_details,
    items_for_day,
    month_days,
    month_marks,
    set_item_audience,
    shift_month,
    tags_split,
)
from app.services.tracker import create_task, delete_task, get_task, resolve_assignees
from app.services.tz import today_msk
from app.services.video_topics import ambiguous_tag_names, count_topic_audience
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
            # Окно по умолчанию — 11:45–18:30 самого дня, а не «сегодня/завтра»,
            # как в старой форме пробников: день здесь уже выбран человеком.
            "mock_defaults": default_schedule_for_day(day),
            "tariff_tags": tariff_tags,
            "other_tags": other_tags,
        },
    )


class TicketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = Field(default=None, max_length=500)
    image_path: str | None = Field(default=None, max_length=300)
    opens_at: str = Field(min_length=1, max_length=32)
    closes_at: str = Field(min_length=1, max_length=32)
    duration_minutes: int = Field(default=90, ge=1, le=720)
    restrict_start_by_duration: bool = True

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class SubjectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=200)
    tickets: list[TicketPayload] = Field(min_length=1, max_length=10)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in MOCK_SUBJECTS:
            raise ValueError("Unknown subject")
        return value


class AudiencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assign_to_all: bool = False
    tag_ids: list[int] = Field(default_factory=list, max_length=200)
    assignee_usernames: str = Field(default="", max_length=20_000)


class MockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjects: list[SubjectPayload] = Field(min_length=1, max_length=len(MOCK_SUBJECTS))
    audience: AudiencePayload = Field(default_factory=AudiencePayload)


def _guard_future(day: date) -> None:
    if day < today_msk():
        raise HTTPException(
            status_code=422, detail="Прошедший день можно только смотреть"
        )


def _resolve_audience(db: DBSession, audience: AudiencePayload) -> tuple[list[int], list[int], list[str]]:
    tag_ids = [] if audience.assign_to_all else validate_tags(db, audience.tag_ids)
    assignee_ids, not_found = ([], [])
    if not audience.assign_to_all:
        assignee_ids, not_found = resolve_assignees(db, audience.assignee_usernames)
    if not audience.assign_to_all and not tag_ids and not assignee_ids:
        raise HTTPException(
            status_code=422,
            detail="Выберите, кому это доступно: теги, отдельные ученики или «всем»",
        )
    return tag_ids, assignee_ids, not_found


@router.post("/{iso}/mock", response_class=JSONResponse)
def create_mock_item(
    iso: str,
    payload: MockPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Пробник в дне: по одному заданию на предмет, билеты и адресация.

    Два предмета в один день — это два `ExamAssignment`: колонка `subject` у
    задания одна и NOT NULL, и разводить их иначе значило бы ломать выдачу
    билетов ученику, которая тоже идёт по предмету.
    """
    day = _parse_day(iso)
    _guard_future(day)
    tag_ids, assignee_ids, not_found = _resolve_audience(db, payload.audience)

    seen: set[str] = set()
    period_start: date | None = None
    period_end: date | None = None

    for block in payload.subjects:
        if block.subject in seen:
            raise HTTPException(
                status_code=422, detail=f"Предмет «{block.subject}» указан дважды"
            )
        seen.add(block.subject)

        note = (block.note or "").strip() or None
        seq = next_seq_number(db, "mock", block.subject)
        assignment = ExamAssignment(
            title=compose_assignment_title("mock", seq, block.subject, day, note),
            subject=block.subject,
            kind="mock",
            seq_number=seq,
            note=note,
            status="published",
            created_by_id=user["user_id"],
        )
        db.add(assignment)
        db.flush()

        latest_close: datetime | None = None
        for number, ticket in enumerate(block.tickets, start=1):
            opens_at = parse_msk_datetime(
                ticket.opens_at, ticket_number=number, field_label="открывается"
            )
            closes_at = parse_msk_datetime(
                ticket.closes_at, ticket_number=number, field_label="закрывается"
            )
            start_date, end_date = validate_window(
                ticket_number=number,
                opens_at=opens_at,
                closes_at=closes_at,
                duration_minutes=ticket.duration_minutes,
                restrict_start_by_duration=ticket.restrict_start_by_duration,
            )
            create_ticket(
                db,
                assignment,
                number=number,
                title=ticket.title,
                description=ticket.description,
                image_url=ticket.image_url,
                image_path=ticket.image_path,
                opens_at=opens_at,
                closes_at=closes_at,
                duration_minutes=ticket.duration_minutes,
                restrict_start_by_duration=ticket.restrict_start_by_duration,
                start_date=start_date,
                end_date=end_date,
                assign_to_all=payload.audience.assign_to_all,
                tag_ids=tag_ids,
                assignee_ids=assignee_ids,
            )
            latest_close = closes_at if latest_close is None else max(latest_close, closes_at)
            period_start = start_date if period_start is None else min(period_start, start_date)
            period_end = end_date if period_end is None else max(period_end, end_date)

        topic = ensure_item_topic(
            db, title=f"Пробник · {block.subject}", day=day, user_id=user["user_id"]
        )
        set_item_audience(
            db,
            topic,
            assign_to_all=payload.audience.assign_to_all,
            tag_ids=tag_ids,
            assignee_ids=assignee_ids,
        )
        task = create_task(
            db,
            title=f"Пробник по предмету «{block.subject}»",
            description=note,
            due_at=latest_close,
            subject=block.subject,
            topic_id=topic.id,
            kind=ITEM_MOCK_EXAM,
            source_kind=SOURCE_EXAM_ASSIGNMENT,
            source_id=assignment.id,
            user_id=user["user_id"],
        )
        task.is_published = True
        db.add(
            AuditLog(
                action="program_mock_create",
                performed_by_id=user["user_id"],
                details=json.dumps(
                    {
                        "day": iso,
                        "subject": block.subject,
                        "assignment_id": assignment.id,
                        "tickets": len(block.tickets),
                    },
                    ensure_ascii=False,
                ),
            )
        )

    if period_start and period_end:
        ensure_mock_period_for(
            db, start_date=period_start, end_date=period_end, user_id=user["user_id"]
        )
    db.commit()
    return JSONResponse(
        {
            "ok": True,
            "not_found": not_found,
            "audience_size": count_topic_audience(
                db,
                assign_to_all=payload.audience.assign_to_all,
                tag_ids=tag_ids,
                assignee_ids=assignee_ids,
            ),
            "ambiguous_tags": ambiguous_tag_names(db, tag_ids),
        }
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
