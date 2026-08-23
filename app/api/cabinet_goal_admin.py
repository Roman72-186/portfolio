"""Экран Главного преподавателя: «Ближайшая цель» на Личном трекере.

Ручная карточка (решение владельца 23.08: не производная от гейта, отдельная
небольшая сущность) — «Пробник по рисунку, с 25 по 30, цель 75 баллов».
Адресация та же тройка, что у задач и дайджеста: явный флаг «всем» + теги +
поимённые исключения.
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
from app.services.tags import get_all_tags
from app.services.tracker import (
    assignee_usernames,
    count_goal_audience,
    create_goal,
    delete_goal,
    get_goal,
    get_goal_assignee_ids,
    get_goal_tag_ids,
    list_goals,
    publish_goal,
    resolve_assignees,
    set_goal_assignees,
    set_goal_tags,
    unpublish_goal,
    update_goal,
)
from app.services.video_topics import ambiguous_tag_names
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/goals")


class GoalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    target_score: int | None = Field(default=None, ge=0, le=100)
    starts_on: date | None = None
    ends_on: date | None = None
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

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("ends_on")
    @classmethod
    def check_range(cls, value: date | None, info) -> date | None:
        starts_on = info.data.get("starts_on")
        if starts_on is not None and value is not None and value < starts_on:
            raise ValueError("ends_on раньше starts_on")
        return value


def _audit(db: DBSession, *, action: str, user_id: int, goal) -> None:
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(
                {"goal_id": goal.id, "title": goal.title[:200]}, ensure_ascii=False
            ),
        )
    )


def _audience_feedback(
    db: DBSession, *, assign_to_all: bool, tag_ids: list[int], assignee_ids: list[int]
) -> dict:
    return {
        "audience_size": count_goal_audience(
            db, assign_to_all=assign_to_all, tag_ids=tag_ids, assignee_ids=assignee_ids
        ),
        "ambiguous_tags": ambiguous_tag_names(db, tag_ids),
    }


def _get_goal_or_404(db: DBSession, goal_id: int):
    goal = get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Цель не найдена")
    return goal


@router.get("", response_class=HTMLResponse)
def goal_admin_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    goals = list_goals(db)
    goal_tag_ids = {g.id: get_goal_tag_ids(db, g.id) for g in goals}
    goal_assignee_ids = {g.id: get_goal_assignee_ids(db, g.id) for g in goals}
    all_tags = get_all_tags(db)
    ambiguous_names = set(ambiguous_tag_names(db, [tag.id for tag in all_tags]))
    return templates.TemplateResponse(
        "cabinet_goal_admin.html",
        {
            "request": request,
            "user": user,
            "goals": goals,
            "all_tags": all_tags,
            "goal_tag_ids": goal_tag_ids,
            "goal_assignee_ids": goal_assignee_ids,
            "goal_assignee_usernames": {
                g.id: assignee_usernames(db, goal_assignee_ids.get(g.id, []))
                for g in goals
            },
            "goal_audience": {
                g.id: count_goal_audience(
                    db,
                    assign_to_all=g.assign_to_all,
                    tag_ids=goal_tag_ids.get(g.id, []),
                    assignee_ids=goal_assignee_ids.get(g.id, []),
                )
                for g in goals
            },
            "goal_ambiguous_tags": {
                g.id: ambiguous_tag_names(db, goal_tag_ids.get(g.id, []))
                for g in goals
            },
            "ambiguous_tag_ids": {
                tag.id for tag in all_tags if tag.name in ambiguous_names
            },
        },
    )


@router.post("", response_class=JSONResponse)
def create_goal_route(
    payload: GoalPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    goal = create_goal(
        db,
        title=payload.title,
        description=payload.description,
        target_score=payload.target_score,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        assign_to_all=payload.assign_to_all,
        user_id=user["user_id"],
    )
    set_goal_tags(db, goal, payload.tag_ids)
    assignee_ids, not_found = resolve_assignees(db, payload.assignee_usernames)
    set_goal_assignees(db, goal, assignee_ids)
    _audit(db, action="goal_create", user_id=user["user_id"], goal=goal)
    feedback = _audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse({"ok": True, "goal_id": goal.id, "not_found": not_found, **feedback})


@router.post("/{goal_id}", response_class=JSONResponse)
def update_goal_route(
    goal_id: int,
    payload: GoalPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    goal = _get_goal_or_404(db, goal_id)
    update_goal(
        goal,
        title=payload.title,
        description=payload.description,
        target_score=payload.target_score,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        assign_to_all=payload.assign_to_all,
    )
    set_goal_tags(db, goal, payload.tag_ids)
    assignee_ids, not_found = resolve_assignees(db, payload.assignee_usernames)
    set_goal_assignees(db, goal, assignee_ids)
    _audit(db, action="goal_update", user_id=user["user_id"], goal=goal)
    feedback = _audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse({"ok": True, "not_found": not_found, **feedback})


@router.post("/{goal_id}/publish", response_class=JSONResponse)
def publish_goal_route(
    goal_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    goal = _get_goal_or_404(db, goal_id)
    try:
        publish_goal(goal, user_id=user["user_id"])
    except ValueError:
        return JSONResponse({"ok": False, "error": "goal_deleted"}, status_code=409)
    _audit(db, action="goal_publish", user_id=user["user_id"], goal=goal)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{goal_id}/unpublish", response_class=JSONResponse)
def unpublish_goal_route(
    goal_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    goal = _get_goal_or_404(db, goal_id)
    unpublish_goal(goal)
    _audit(db, action="goal_unpublish", user_id=user["user_id"], goal=goal)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{goal_id}/delete", response_class=JSONResponse)
def delete_goal_route(
    goal_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    goal = _get_goal_or_404(db, goal_id)
    if goal.is_published:
        return JSONResponse({"ok": False, "error": "unpublish_first"}, status_code=409)
    delete_goal(goal)
    _audit(db, action="goal_delete", user_id=user["user_id"], goal=goal)
    db.commit()
    return JSONResponse({"ok": True})
