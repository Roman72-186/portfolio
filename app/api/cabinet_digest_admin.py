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
from app.models.tracker import EVENT_KIND_LABELS, EVENT_KINDS
from app.services.tags import get_all_tags
from app.services.tracker import (
    assignee_usernames,
    count_digest_audience,
    create_digest,
    create_event,
    delete_digest,
    delete_event,
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
    unpublish_digest,
    update_digest,
    update_event,
)
from app.services.video_topics import ambiguous_tag_names
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/digest")

MONTH_NAMES = (
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


class DigestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
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


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=20)
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)
    starts_on: date
    ends_on: date
    meeting_url: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(default=0, ge=0, le=1000)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in EVENT_KINDS:
            raise ValueError("Unknown event kind")
        return value

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
        value = (value or "").strip()
        return value or None

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
    return templates.TemplateResponse(
        "cabinet_digest_admin.html",
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
):
    digest = _get_digest_or_404(db, digest_id)
    events = list_events(db, digest_id)
    return templates.TemplateResponse(
        "cabinet_digest_events.html",
        {
            "request": request,
            "user": user,
            "digest": digest,
            "events": events,
            "event_kinds": EVENT_KINDS,
            "event_kind_labels": EVENT_KIND_LABELS,
            "month_names": MONTH_NAMES,
        },
    )


def _event_of_digest_or_404(db: DBSession, digest_id: int, event_id: int):
    event = get_event(db, event_id)
    if event is None or event.digest_id != digest_id:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return event


@router.post("/{digest_id}/events", response_class=JSONResponse)
def create_digest_event(
    digest_id: int,
    payload: EventPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    _get_digest_or_404(db, digest_id)
    event = create_event(
        db,
        digest_id,
        kind=payload.kind,
        title=payload.title,
        note=payload.note,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        meeting_url=payload.meeting_url,
        sort_order=payload.sort_order,
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
    update_event(
        event,
        kind=payload.kind,
        title=payload.title,
        note=payload.note,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        meeting_url=payload.meeting_url,
        sort_order=payload.sort_order,
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
    delete_event(db, event)
    db.commit()
    return JSONResponse({"ok": True})
