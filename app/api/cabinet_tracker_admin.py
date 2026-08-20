"""Экран Главного преподавателя: задачи «Личного трекера».

Роль в коде называется «админ» (ранг 4), владелец говорит «Главный
преподаватель» — это про подпись в интерфейсе, переименование роли трогает пять
мест и идёт отдельной задачей.

Файл новый намеренно: `cabinet_admin.py` ведёт параллельная сессия по
уведомлениям, и дописывать туда — гарантированный конфликт
(plans/2026-08-20-apparchi-tracker-and-digest.md, раздел «Границы»).
"""

import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header
from app.models.audit_log import AuditLog
from app.services.tags import get_all_tags
from app.services.tracker import (
    assignee_usernames,
    count_completed,
    count_task_audience,
    create_task,
    delete_task,
    get_assignee_ids,
    get_tag_ids,
    get_task,
    list_tasks,
    publish_task,
    resolve_assignees,
    set_task_assignees,
    set_task_tags,
    unpublish_task,
    update_task,
)
from app.services.tz import MSK_TZ
# Подсказка про «Р»/«К» общая с видеоуроками: в проде это группа и уровень
# куратора, а не предмет. Правило доступа от неё не зависит, поэтому берём
# готовую функцию, а не копируем список букв во второе место.
from app.services.video_topics import ambiguous_tag_names
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/tracker")


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    # Локальное время МСК из <input type="datetime-local">. Пусто — поля нет:
    # задача без дедлайна допустима, старт без значения означает «сразу после
    # публикации».
    due_at: str = Field(default="", max_length=32)
    starts_at: str = Field(default="", max_length=32)
    subject: str | None = Field(default=None, max_length=50)
    assign_to_all: bool = False
    tag_ids: list[int] = Field(default_factory=list, max_length=200)
    # Поимённые исключения — списком @username, тем же способом, каким владелец
    # уже раздаёт теги и темы видеоуроков.
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

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if value not in MOCK_SUBJECTS:
            raise ValueError("Unknown subject")
        return value


def _parse_msk_datetime(raw: str, *, field: str) -> datetime | None:
    """Строку из формы трактуем как московское время. Пусто — значения нет.

    В контейнере UTC, и без явной таймзоны задача краснела бы на три часа раньше
    срока — та же ловушка, из-за которой в проекте всюду используется tz.py.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Неверная дата: {field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MSK_TZ)
    return parsed


def _format_msk_datetime_local(value: datetime | None) -> str:
    """Значение из базы → строка для `<input type="datetime-local">` в МСК.

    Колонка `TIMESTAMPTZ`, Postgres отдаёт её в таймзоне сессии (в контейнере
    UTC). Отдать это в форму как есть нельзя: ввод трактуется как московское
    время, и каждое повторное сохранение уводило бы дедлайн на три часа назад.
    На темах видеоуроков это уже ловили.
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MSK_TZ).strftime("%Y-%m-%dT%H:%M")


def _format_msk_display(value: datetime | None) -> str:
    """Значение из базы → человекочитаемое московское время для списка."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MSK_TZ).strftime("%d.%m.%Y %H:%M")


def _audit_task(db: DBSession, *, action: str, user_id: int, task) -> None:
    """Пишем в журнал каждое действие над задачей: все они меняют, кто и что
    увидит, — по той же причине аудируются темы видеоуроков."""
    db.add(
        AuditLog(
            action=action,
            performed_by_id=user_id,
            details=json.dumps(
                {"task_id": task.id, "title": task.title[:200]}, ensure_ascii=False
            ),
        )
    )


def _audience_feedback(
    db: DBSession, *, assign_to_all: bool, tag_ids: list[int], assignee_ids: list[int]
) -> dict:
    """Охват и спорные теги — чтобы промах адресации был виден до публикации."""
    return {
        "audience_size": count_task_audience(
            db,
            assign_to_all=assign_to_all,
            tag_ids=tag_ids,
            assignee_ids=assignee_ids,
        ),
        "ambiguous_tags": ambiguous_tag_names(db, tag_ids),
    }


def _get_task_or_404(db: DBSession, task_id: int):
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


def _check_period(due_at: datetime | None, starts_at: datetime | None) -> None:
    if due_at is not None and starts_at is not None and starts_at > due_at:
        raise HTTPException(
            status_code=422, detail="Задача открывается позже своего дедлайна"
        )


@router.get("", response_class=HTMLResponse)
def tracker_admin_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    tasks = list_tasks(db)
    task_tag_ids = {t.id: get_tag_ids(db, t.id) for t in tasks}
    task_assignee_ids = {t.id: get_assignee_ids(db, t.id) for t in tasks}
    all_tags = get_all_tags(db)
    ambiguous_names = set(ambiguous_tag_names(db, [tag.id for tag in all_tags]))
    return templates.TemplateResponse(
        "cabinet_tracker_admin.html",
        {
            "request": request,
            "user": user,
            "tasks": tasks,
            "all_tags": all_tags,
            "subjects": MOCK_SUBJECTS,
            "task_tag_ids": task_tag_ids,
            "task_assignee_ids": task_assignee_ids,
            # Даты форматируются здесь, а не в шаблоне: колонка TIMESTAMPTZ
            # приезжает в таймзоне сессии, а форма трактует ввод как МСК.
            "task_due_form": {t.id: _format_msk_datetime_local(t.due_at) for t in tasks},
            "task_due_display": {t.id: _format_msk_display(t.due_at) for t in tasks},
            "task_starts_form": {
                t.id: _format_msk_datetime_local(t.starts_at) for t in tasks
            },
            "task_starts_display": {
                t.id: _format_msk_display(t.starts_at) for t in tasks
            },
            # Поимённые ученики возвращаются в форму, иначе сохранение стирает их.
            "task_assignee_usernames": {
                t.id: assignee_usernames(db, task_assignee_ids.get(t.id, []))
                for t in tasks
            },
            # Охват и спорные теги считаются на сервере: промах адресации должен
            # быть виден до публикации, а не после жалоб учеников.
            "task_audience": {
                t.id: count_task_audience(
                    db,
                    assign_to_all=t.assign_to_all,
                    tag_ids=task_tag_ids.get(t.id, []),
                    assignee_ids=task_assignee_ids.get(t.id, []),
                )
                for t in tasks
            },
            "task_done": {t.id: count_completed(db, t.id) for t in tasks},
            "task_ambiguous_tags": {
                t.id: ambiguous_tag_names(db, task_tag_ids.get(t.id, []))
                for t in tasks
            },
            "ambiguous_tag_ids": {
                tag.id for tag in all_tags if tag.name in ambiguous_names
            },
        },
    )


@router.post("/tasks", response_class=JSONResponse)
def create_tracker_task(
    payload: TaskPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    due_at = _parse_msk_datetime(payload.due_at, field="дедлайн")
    starts_at = _parse_msk_datetime(payload.starts_at, field="начало")
    _check_period(due_at, starts_at)

    task = create_task(
        db,
        title=payload.title,
        description=payload.description,
        due_at=due_at,
        starts_at=starts_at,
        subject=payload.subject,
        assign_to_all=payload.assign_to_all,
        user_id=user["user_id"],
    )
    set_task_tags(db, task, payload.tag_ids)
    assignee_ids, not_found = resolve_assignees(db, payload.assignee_usernames)
    set_task_assignees(db, task, assignee_ids)
    _audit_task(db, action="tracker_task_create", user_id=user["user_id"], task=task)
    feedback = _audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse(
        {"ok": True, "task_id": task.id, "not_found": not_found, **feedback}
    )


@router.post("/tasks/{task_id}", response_class=JSONResponse)
def update_tracker_task(
    task_id: int,
    payload: TaskPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = _get_task_or_404(db, task_id)
    due_at = _parse_msk_datetime(payload.due_at, field="дедлайн")
    starts_at = _parse_msk_datetime(payload.starts_at, field="начало")
    _check_period(due_at, starts_at)

    update_task(
        task,
        title=payload.title,
        description=payload.description,
        due_at=due_at,
        starts_at=starts_at,
        subject=payload.subject,
        assign_to_all=payload.assign_to_all,
    )
    set_task_tags(db, task, payload.tag_ids)
    assignee_ids, not_found = resolve_assignees(db, payload.assignee_usernames)
    set_task_assignees(db, task, assignee_ids)
    _audit_task(db, action="tracker_task_update", user_id=user["user_id"], task=task)
    feedback = _audience_feedback(
        db,
        assign_to_all=payload.assign_to_all,
        tag_ids=payload.tag_ids,
        assignee_ids=assignee_ids,
    )
    db.commit()
    return JSONResponse({"ok": True, "not_found": not_found, **feedback})


@router.post("/tasks/{task_id}/publish", response_class=JSONResponse)
def publish_tracker_task(
    task_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = _get_task_or_404(db, task_id)
    try:
        publish_task(task, user_id=user["user_id"])
    except ValueError:
        return JSONResponse({"ok": False, "error": "task_deleted"}, status_code=409)
    _audit_task(db, action="tracker_task_publish", user_id=user["user_id"], task=task)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/tasks/{task_id}/unpublish", response_class=JSONResponse)
def unpublish_tracker_task(
    task_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = _get_task_or_404(db, task_id)
    unpublish_task(task)
    _audit_task(db, action="tracker_task_unpublish", user_id=user["user_id"], task=task)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/tasks/{task_id}/delete", response_class=JSONResponse)
def delete_tracker_task(
    task_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Мягкое удаление, и только со снятой публикации.

    Опубликованная задача уже висит у учеников в трекере: удаление одним
    движением убрало бы её с экрана без следа для тех, кто её ещё не сделал.
    Сначала снять публикацию — это видимое действие с понятным результатом.
    """
    task = _get_task_or_404(db, task_id)
    if task.is_published:
        return JSONResponse({"ok": False, "error": "unpublish_first"}, status_code=409)
    delete_task(task)
    _audit_task(db, action="tracker_task_delete", user_id=user["user_id"], task=task)
    db.commit()
    return JSONResponse({"ok": True})
