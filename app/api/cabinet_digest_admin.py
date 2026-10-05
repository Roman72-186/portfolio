"""Экран Главного преподавателя: дайджест-расписание месяца.

Дайджест — статичный блок на месяц (утверждается в конце месяца, публикуется
в начале, весь месяц не меняется), список событий внутри (дедлайн/занятие/
пробник/эфир). Адресация та же тройка, что у задач трекера и тем видеоуроков:
явный флаг «всем» + теги + поимённые исключения — решение владельца 20.08.

Файл новый и отдельный от `cabinet_tracker_admin.py` по той же причине, по
которой тот отдельный от `cabinet_admin.py`: дайджест — самостоятельный
экран со своим списком и своей формой, совмещать со списком задач в одном
файле только ради общего раздела меню незачем.
"""

import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header
from app.models.audit_log import AuditLog
from app.constants import TARIFF_DISPLAY, TARIFFS, TARIFFS_CURRENT
from app.models.tracker import EVENT_COLOR_DEFAULT, EVENT_PALETTE, EVENT_STYLE_FILL, EVENT_STYLES
from app.services.section_access import can
from app.services.schedule_event_types import (
    EventTypeInUse,
    archive_type,
    create_type,
    delete_type,
    get_type,
    list_types,
    move_type,
    restore_type,
    type_usage,
    update_type,
)
from app.services.tags import get_all_tags
from app.services.tracker import (
    assignee_usernames,
    count_digest_audience,
    create_digest,
    create_event,
    delete_digest,
    delete_event,
    digest_calendar,
    digest_heading,
    event_tariffs_map,
    events_for_tariff,
    format_event_dates,
    get_digest,
    get_digest_assignee_ids,
    get_digest_tag_ids,
    get_event,
    list_digests,
    list_events,
    publish_digest,
    resolve_assignees,
    set_digest_assignees,
    set_digest_tags,
    set_event_tariffs,
    unpublish_digest,
    update_digest,
    update_event,
)
from app.services.video_topics import ambiguous_tag_names
from app.services.program import WEEKDAY_LABELS
from app.services.tz import today_msk
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/digest")

MONTH_NAMES = (
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


class DigestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    # Тема месяца: её ученик читает над календарём. Необязательна — у
    # дайджестов, заведённых до 16.09.2026, темы нет, им показывается title.
    theme: str | None = Field(default=None, max_length=120)
    year: int = Field(ge=2020, le=2100)
    month: int = Field(ge=1, le=12)
    assign_to_all: bool = False
    tag_ids: list[int] = Field(default_factory=list, max_length=200)
    assignee_usernames: str = Field(default="", max_length=20_000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("theme")
    @classmethod
    def strip_theme(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Тип задаёт и подпись, и цвет метки (владелец 04.10.2026). Существует ли
    # тип и не скрыт ли он — проверяет роут, схеме база недоступна.
    type_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)
    starts_on: date
    ends_on: date
    meeting_url: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(default=0, ge=0, le=1000)
    # Тарифы, которым событие показывается (созвон 30.09.2026). Пусто — всем.
    tariffs: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("tariffs")
    @classmethod
    def validate_tariffs(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip().upper() for t in value if t and t.strip()]
        unknown = [t for t in cleaned if t not in TARIFFS]
        if unknown:
            raise ValueError("Неизвестный тариф: " + ", ".join(unknown))
        return list(dict.fromkeys(cleaned))

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("meeting_url")
    @classmethod
    def strip_url(cls, value: str | None) -> str | None:
        """Только http и https: ссылка уходит в `href` у каждого ученика из
        адресатов дайджеста, и схема `javascript:` выполнила бы код у того,
        кто нажмёт (код-ревью 28.09.2026, P2). Та же проверка, что у ссылки
        в блоке конструктора (`cabinet_program.py::validate_url`)."""
        value = (value or "").strip()
        if not value:
            return None
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Ссылка на созвон должна начинаться с http:// или https://")
        return value

    @field_validator("ends_on")
    @classmethod
    def check_range(cls, value: date, info) -> date:
        starts_on = info.data.get("starts_on")
        if starts_on is not None and value < starts_on:
            raise ValueError("ends_on раньше starts_on")
        return value


def _audit(db: DBSession, *, action: str, user_id: int, digest) -> None:
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(
                {"digest_id": digest.id, "title": digest.title[:200]}, ensure_ascii=False
            ),
        )
    )


def _audience_feedback(
    db: DBSession, *, assign_to_all: bool, tag_ids: list[int], assignee_ids: list[int]
) -> dict:
    return {
        "audience_size": count_digest_audience(
            db, assign_to_all=assign_to_all, tag_ids=tag_ids, assignee_ids=assignee_ids
        ),
        "ambiguous_tags": ambiguous_tag_names(db, tag_ids),
    }


def _get_digest_or_404(db: DBSession, digest_id: int):
    digest = get_digest(db, digest_id)
    if digest is None:
        raise HTTPException(status_code=404, detail="Дайджест не найден")
    return digest


@router.get("", response_class=HTMLResponse)
def digest_admin_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    digests = list_digests(db)
    digest_tag_ids = {d.id: get_digest_tag_ids(db, d.id) for d in digests}
    digest_assignee_ids = {d.id: get_digest_assignee_ids(db, d.id) for d in digests}
    all_tags = get_all_tags(db)
    ambiguous_names = set(ambiguous_tag_names(db, [tag.id for tag in all_tags]))
    return templates.TemplateResponse(request, "cabinet_digest_admin.html",
        {
            "request": request,
            "user": user,
            "digests": digests,
            "all_tags": all_tags,
            "month_names": MONTH_NAMES,
            "digest_tag_ids": digest_tag_ids,
            "digest_assignee_ids": digest_assignee_ids,
            "digest_assignee_usernames": {
                d.id: assignee_usernames(db, digest_assignee_ids.get(d.id, []))
                for d in digests
            },
            "digest_audience": {
                d.id: count_digest_audience(
                    db,
                    assign_to_all=d.assign_to_all,
                    tag_ids=digest_tag_ids.get(d.id, []),
                    assignee_ids=digest_assignee_ids.get(d.id, []),
                )
                for d in digests
            },
            "digest_event_count": {d.id: len(list_events(db, d.id)) for d in digests},
            "digest_ambiguous_tags": {
                d.id: ambiguous_tag_names(db, digest_tag_ids.get(d.id, []))
                for d in digests
            },
            "ambiguous_tag_ids": {
                tag.id for tag in all_tags if tag.name in ambiguous_names
            },
        },
    )


# ---------------------------------------------------------------------------
# Типы событий: название + цвет + заливка/контур (владелец 04.10.2026).
# Объявлены до `/{digest_id}`: иначе «types» поймал бы путь с номером
# дайджеста и ответил 422.
# ---------------------------------------------------------------------------

class EventTypePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default=EVENT_COLOR_DEFAULT, max_length=16)
    style: str = Field(default=EVENT_STYLE_FILL, max_length=8)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Укажите название типа")
        return value

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        if value not in EVENT_PALETTE:
            raise ValueError("Неизвестный цвет")
        return value

    @field_validator("style")
    @classmethod
    def validate_style(cls, value: str) -> str:
        if value not in EVENT_STYLES:
            raise ValueError("Неизвестный стиль метки")
        return value


class MovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: int = Field(ge=-1, le=1)


def _type_or_404(db: DBSession, type_id: int):
    event_type = get_type(db, type_id)
    if event_type is None:
        raise HTTPException(status_code=404, detail="Тип не найден")
    return event_type


def _audit_type(db: DBSession, *, action: str, user_id: int, event_type) -> None:
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(
                {"event_type_id": event_type.id, "name": event_type.name}, ensure_ascii=False
            ),
        )
    )


@router.get("/types", response_class=HTMLResponse)
def event_types_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    back = request.query_params.get("digest", "")
    return templates.TemplateResponse(request, "cabinet_digest_types.html",
        {
            "request": request,
            "user": user,
            "event_types": list_types(db, include_archived=True),
            "type_usage": type_usage(db),
            "palette": EVENT_PALETTE,
            "styles": EVENT_STYLES,
            # Откуда пришли: со страницы событий дайджеста — туда и «назад».
            "back_digest_id": int(back) if back.isdigit() else None,
        },
    )


@router.post("/types", response_class=JSONResponse)
def create_event_type_route(
    payload: EventTypePayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    event_type = create_type(db, name=payload.name, color=payload.color, style=payload.style)
    _audit_type(db, action="event_type_create", user_id=user["user_id"], event_type=event_type)
    db.commit()
    return JSONResponse({"ok": True, "type_id": event_type.id})


@router.post("/types/{type_id}", response_class=JSONResponse)
def update_event_type_route(
    type_id: int,
    payload: EventTypePayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    event_type = _type_or_404(db, type_id)
    update_type(event_type, name=payload.name, color=payload.color, style=payload.style)
    _audit_type(db, action="event_type_update", user_id=user["user_id"], event_type=event_type)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/types/{type_id}/archive", response_class=JSONResponse)
def archive_event_type_route(
    type_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    event_type = _type_or_404(db, type_id)
    archive_type(event_type)
    _audit_type(db, action="event_type_archive", user_id=user["user_id"], event_type=event_type)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/types/{type_id}/restore", response_class=JSONResponse)
def restore_event_type_route(
    type_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    event_type = _type_or_404(db, type_id)
    restore_type(event_type)
    _audit_type(db, action="event_type_restore", user_id=user["user_id"], event_type=event_type)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/types/{type_id}/delete", response_class=JSONResponse)
def delete_event_type_route(
    type_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    event_type = _type_or_404(db, type_id)
    try:
        delete_type(db, event_type)
    except EventTypeInUse:
        return JSONResponse({"ok": False, "error": "type_in_use"}, status_code=409)
    _audit_type(db, action="event_type_delete", user_id=user["user_id"], event_type=event_type)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/types/{type_id}/move", response_class=JSONResponse)
def move_event_type_route(
    type_id: int,
    payload: MovePayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    event_type = _type_or_404(db, type_id)
    move_type(db, event_type, payload.direction)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("", response_class=JSONResponse)
def create_digest_route(
    payload: DigestPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    digest = create_digest(
        db,
        title=payload.title,
        theme=payload.theme,
        year=payload.year,
        month=payload.month,
        assign_to_all=payload.assign_to_all,
        user_id=user["user_id"],
    )
    set_digest_tags(db, digest, payload.tag_ids)
    assignee_ids, not_found = resolve_assignees(db, payload.assignee_usernames)
    set_digest_assignees(db, digest, assignee_ids)
    _audit(db, action="digest_create", user_id=user["user_id"], digest=digest)
    feedback = _audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse({"ok": True, "digest_id": digest.id, "not_found": not_found, **feedback})


@router.post("/{digest_id}", response_class=JSONResponse)
def update_digest_route(
    digest_id: int,
    payload: DigestPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    digest = _get_digest_or_404(db, digest_id)
    update_digest(
        digest,
        title=payload.title,
        theme=payload.theme,
        year=payload.year,
        month=payload.month,
        assign_to_all=payload.assign_to_all,
    )
    set_digest_tags(db, digest, payload.tag_ids)
    assignee_ids, not_found = resolve_assignees(db, payload.assignee_usernames)
    set_digest_assignees(db, digest, assignee_ids)
    _audit(db, action="digest_update", user_id=user["user_id"], digest=digest)
    feedback = _audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse({"ok": True, "not_found": not_found, **feedback})


@router.post("/{digest_id}/publish", response_class=JSONResponse)
def publish_digest_route(
    digest_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    digest = _get_digest_or_404(db, digest_id)
    try:
        publish_digest(digest, user_id=user["user_id"])
    except ValueError:
        return JSONResponse({"ok": False, "error": "digest_deleted"}, status_code=409)
    _audit(db, action="digest_publish", user_id=user["user_id"], digest=digest)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{digest_id}/unpublish", response_class=JSONResponse)
def unpublish_digest_route(
    digest_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    digest = _get_digest_or_404(db, digest_id)
    unpublish_digest(digest)
    _audit(db, action="digest_unpublish", user_id=user["user_id"], digest=digest)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{digest_id}/delete", response_class=JSONResponse)
def delete_digest_route(
    digest_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    digest = _get_digest_or_404(db, digest_id)
    if digest.is_published:
        return JSONResponse({"ok": False, "error": "unpublish_first"}, status_code=409)
    delete_digest(digest)
    _audit(db, action="digest_delete", user_id=user["user_id"], digest=digest)
    db.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# События внутри дайджеста
# ---------------------------------------------------------------------------

@router.get("/{digest_id}/events", response_class=HTMLResponse)
def digest_events_page(
    digest_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    tariff: str | None = None,
):
    """Редактор месяца: календарь первым экраном, тап по дате открывает день
    (владелец 04.10.2026: «нажимаю на нужную дату и внутри неё создаю
    событие»). Календарь — тот же партиал, что у ученика, поэтому редактор и
    есть предпросмотр; ученик видит из него только общие события и события
    своего тарифа.

    `?tariff=` — месяц глазами ученика одного тарифа (служба заботы
    04.10.2026: «видеть 3 отдельных календаря по тарифам, чтобы можно было
    делать скрин»). Отбор тот же `events_for_tariff`, что у ученика, и он
    режет всю страницу — сетку, список и панель дня, иначе панель показала
    бы событие, которого нет в клетке. Незнакомый тариф — все события."""
    digest = _get_digest_or_404(db, digest_id)
    events = list_events(db, digest_id)
    tariff_view = tariff if tariff in TARIFFS_CURRENT else None
    if tariff_view:
        events = events_for_tariff(db, events, tariff_view)
    tariffs = event_tariffs_map(db, [event.id for event in events])
    return templates.TemplateResponse(request, "cabinet_digest_events.html",
        {
            "request": request,
            "user": user,
            "digest": digest,
            "event_types": list_types(db),
            "tariff_choices": TARIFFS_CURRENT,
            "tariff_display": TARIFF_DISPLAY,
            "tariff_view": tariff_view,
            "month_names": MONTH_NAMES,
            # Данные событий для формы — одним JSON, а не data-атрибутами:
            # строка списка общая с учеником, служебному в ней не место.
            "events_payload": [
                {
                    "id": event.id,
                    "title": event.title,
                    "type_id": event.type_id,
                    "type_name": event.type.name,
                    "type_color": event.type.color,
                    "type_style": event.type.style,
                    "type_archived": event.type.archived_at is not None,
                    "starts_on": event.starts_on.isoformat(),
                    "ends_on": event.ends_on.isoformat(),
                    "dates": format_event_dates(event),
                    "note": event.note or "",
                    "meeting_url": event.meeting_url or "",
                    "tariffs": tariffs.get(event.id, []),
                }
                for event in events
            ],
            "digest_heading": digest_heading(digest),
            "digest_events": events,
            "digest_days": digest_calendar(digest, events, today=today_msk()),
            "digest_weekday_labels": WEEKDAY_LABELS,
            "format_event_dates": format_event_dates,
            # «Смотреть» в АОП — календарь как у ученика, без правки
            # (шаг 2 плана тонких доступов).
            "digest_editable": can(user, "program"),
        },
    )


def _event_of_digest_or_404(db: DBSession, digest_id: int, event_id: int):
    event = get_event(db, event_id)
    if event is None or event.digest_id != digest_id:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return event


def _check_event_type(db: DBSession, type_id: int, *, keep_type_id: int | None = None):
    """Тип должен существовать и не быть скрытым. Скрытый пропускается только
    у события, которое уже на нём стоит и тип не меняет: правка названия
    старого события не должна заставлять его перекрашивать. Возвращает тип —
    его имя уходит в журнал."""
    event_type = get_type(db, type_id)
    if event_type is None:
        raise HTTPException(status_code=422, detail="Такого типа события нет")
    if event_type.archived_at is not None and type_id != keep_type_id:
        raise HTTPException(status_code=422, detail="Этот тип скрыт — выберите другой")
    return event_type


def _audit_event(db: DBSession, *, action: str, user_id: int, event, type_name: str) -> None:
    """След события дайджеста в журнале. До 05.10.2026 его не было: двадцать
    событий, заведённых 04.10, восстанавливали по `created_at` и логам
    запросов, а у события нет ни автора, ни времени правки. Даты — как их
    видит ученик, тип — словом: id типа в журнале ничего не скажет."""
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(
                {
                    "digest_id": event.digest_id,
                    "event_id": event.id,
                    "title": event.title[:200],
                    "starts_on": event.starts_on.isoformat(),
                    "ends_on": event.ends_on.isoformat(),
                    "type": type_name,
                },
                ensure_ascii=False,
            ),
        )
    )


@router.post("/{digest_id}/events", response_class=JSONResponse)
def create_digest_event(
    digest_id: int,
    payload: EventPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    _get_digest_or_404(db, digest_id)
    event_type = _check_event_type(db, payload.type_id)
    event = create_event(
        db,
        digest_id,
        type_id=payload.type_id,
        title=payload.title,
        note=payload.note,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        meeting_url=payload.meeting_url,
        sort_order=payload.sort_order,
    )
    set_event_tariffs(db, event, payload.tariffs)
    _audit_event(
        db, action="digest_event_create", user_id=user["user_id"], event=event,
        type_name=event_type.name,
    )
    db.commit()
    return JSONResponse({"ok": True, "event_id": event.id})


@router.post("/{digest_id}/events/{event_id}", response_class=JSONResponse)
def update_digest_event(
    digest_id: int,
    event_id: int,
    payload: EventPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    _get_digest_or_404(db, digest_id)
    event = _event_of_digest_or_404(db, digest_id, event_id)
    event_type = _check_event_type(db, payload.type_id, keep_type_id=event.type_id)
    update_event(
        event,
        type_id=payload.type_id,
        title=payload.title,
        note=payload.note,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        meeting_url=payload.meeting_url,
        sort_order=payload.sort_order,
    )
    set_event_tariffs(db, event, payload.tariffs)
    _audit_event(
        db, action="digest_event_update", user_id=user["user_id"], event=event,
        type_name=event_type.name,
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{digest_id}/events/{event_id}/delete", response_class=JSONResponse)
def delete_digest_event(
    digest_id: int,
    event_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    _get_digest_or_404(db, digest_id)
    event = _event_of_digest_or_404(db, digest_id, event_id)
    _audit_event(
        db, action="digest_event_delete", user_id=user["user_id"], event=event,
        type_name=event.type.name,
    )
    delete_event(db, event)
    db.commit()
    return JSONResponse({"ok": True})
